# transcript_service.py
import requests
from flask import Flask, request, jsonify, render_template, Response, send_file
from pyyoutube import Client, Api
from dotenv import load_dotenv
from youtube_transcript_api import YouTubeTranscriptApi
import os
import json
import boto3
from botocore.exceptions import NoCredentialsError, PartialCredentialsError
from flask_restx import Api, Resource, fields, reqparse
from youtube_search import display_yt_results, search_yt
import uuid
import threading
import time
from urllib.parse import urlparse, parse_qs
import googleapiclient.discovery
from anthropic import Anthropic
from elevenlabs.client import ElevenLabs
from elevenlabs import stream
import click
load_dotenv()  

YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
ELEVEN_LABS_API_KEY = os.getenv('XI_API_KEY')
anthropic = Anthropic(api_key=ANTHROPIC_API_KEY)

app = Flask(__name__)

# https://elevenlabs.io/app/voice-lab/share/80d9191368f6824d0aacf4080a0894aecb0c4bdd82928eb623e585caf54a84b7/FTNCalFNG5bRnkkaP5Ug
voice_id = "FTNCalFNG5bRnkkaP5Ug"

# Initialize ElevenLabs client
client = ElevenLabs(api_key=ELEVEN_LABS_API_KEY)

# Initialize the Flask-RestX Api after the Flask app
api = Api(app, 
          version='1.0', 
          title='Transcript Service API',
          description='A simple API for managing transcripts and training data',
          prefix='/api',  # Add a prefix for all API routes
          doc='/swagger')  # Swagger UI path

# Initialize YouTube API client
youtube = googleapiclient.discovery.build(
    serviceName='youtube', 
    version='v3', 
    developerKey=YOUTUBE_API_KEY
)

# Define namespaces
ns_transcripts = api.namespace('transcripts', description='Transcript operations')
ns_training = api.namespace('training', description='Training data operations')
ns_jobs = api.namespace('jobs', description='Job operations')

# Define models
transcript_model = api.model('Transcript', {
    'title': fields.String(required=True, description='Video title'),
    'url': fields.String(required=True, description='Video URL'),
    'transcript': fields.String(required=True, description='Video transcript')
})

# Define models for job responses
job_model = api.model('Job', {
    'job_id': fields.String(required=True, description='Unique job identifier'),
    'status': fields.String(required=True, description='Job status'),
    'total_videos': fields.Integer(required=True, description='Total number of videos to process'),
    'processed_videos': fields.Integer(required=True, description='Number of videos processed'),
    'results': fields.List(fields.Nested(api.model('TranscriptResult', {
        'video_id': fields.String(required=True, description='YouTube video ID'),
        'title': fields.String(required=True, description='Video title'),
        'transcript': fields.String(required=True, description='Video transcript')
    })))
})

# Initialize the S3 client
session = boto3.Session(profile_name='knowledge-collector')
s3 = session.client('s3', region_name='us-west-2')
BUCKET_NAME = 'jmhudak-knowledge-collector'  # Replace with your S3 bucket name

# Global dictionary to store job statuses and results
jobs = {}

def fetch_transcripts(channel_name, author=None):
    try:
        print(f"CHANNEL NAME {channel_name}")

        search_response = search_yt(channel_name)
        if search_response is None:
            print("search_yt returned None")
            return {"error": "No search results found"}, 404

        search_results = display_yt_results(search_response)
        if search_results is None:
            print("display_yt_results returned None")
            return {"error": "Failed to process search results"}, 500

        print(f"Search results: {search_results}")  # Debug print

        # Filter videos by author matching channel_name
        filtered_results = [
            result for result in search_results 
            if result.get('author', '').lower() == author.lower()
        ]

        if not filtered_results:
            print(f"No videos found for channel: {channel_name}")
            return {"error": f"No videos found for channel: {channel_name}"}, 404

        # Generate a unique job ID
        job_id = str(uuid.uuid4())

        # Start the transcription process in a background thread
        threading.Thread(target=process_transcripts, args=(job_id, filtered_results, channel_name)).start()

        return {
            "job_id": job_id,
            "message": "Transcription job started",
            "total_videos": len(filtered_results)
        }, 202

    except Exception as e:
        print(f"Error fetching transcripts: {e}")
        return {"error": str(e)}, 500

def process_transcripts(job_id, videos, channel_name):
    jobs[job_id] = {
        'job_id': job_id,
        'status': 'in_progress',
        'total_videos': len(videos),
        'processed_videos': 0,
        'results': []
    }

    for video in videos:
        try:
            video_id = video['video_id']
            cache_dir = os.path.join('storage/cache', channel_name)
            os.makedirs(cache_dir, exist_ok=True)
            cache_file_path = os.path.join(cache_dir, f"{video_id}.json")

            if os.path.exists(cache_file_path):
                # Load transcript from cache if it exists
                with open(cache_file_path, 'r') as cache_file:
                    transcript_data = json.load(cache_file)
                    transcript = transcript_data['transcript']
            else:
                # Fetch and process the transcript
                transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
                transcript = " ".join([t['text'] for t in transcript_list])
                
                # Save the transcript to cache
                with open(cache_file_path, 'w') as cache_file:
                    json.dump({'transcript': transcript}, cache_file)

            jobs[job_id]['results'].append({
                'video_id': video_id,
                'title': video['title'],
                'transcript': transcript
            })
            jobs[job_id]['processed_videos'] += 1

        except Exception as e:
            print(f"Error processing video {video['video_id']}: {e}")

    jobs[job_id]['status'] = 'completed'

@app.route('/job_status/<job_id>', methods=['GET'])
def check_job_status(job_id):
    if job_id not in jobs:
        return jsonify({"error": "Job not found"}), 404

    job_status = jobs[job_id]
    response = {
        "status": job_status["status"],
        "total_videos": job_status["total_videos"],
        "processed_videos": job_status["processed_videos"],
        "progress": f"{job_status['processed_videos']}/{job_status['total_videos']}"
    }

    if job_status["status"] == "completed":
        response["transcripts"] = job_status["transcripts"]

    return jsonify(response)

@ns_transcripts.route('/')
class TranscriptList(Resource):
    @api.doc(params={'channel_name': 'YouTube channel name', 'author': 'YouTube author name'})
    @api.response(200, 'Success', [transcript_model])
    def get(self):
        """Fetch transcripts for a given channel"""
        channel_name = request.args.get('channel_name')
        author = request.args.get('author')
        if not channel_name:
            api.abort(400, "Please provide a channel name.")

        return fetch_transcripts(channel_name, author)

@app.route('/transcripts', methods=['GET'])
def get_transcripts():
    channel_name = request.args.get('channel_name')
    author = request.args.get('author')
    if not channel_name:
        return jsonify({"error": "Please provide a channel name."}), 400

    return fetch_transcripts(channel_name, author)

@app.route('/')
def index():
    channel_name = request.args.get('channel_name')  # Get channel_name from query parameters
    author = request.args.get('author')  # Get author from query parameters
    transcripts = None
    if channel_name:  # Only fetch transcripts if channel_name is provided
        transcripts, _ = fetch_transcripts(channel_name, author)
    return render_template('index.html', transcripts=transcripts)

def prepare_finetuning_data(channel_name):
    try:
        print(f"CHANNEL NAME: {channel_name}")
        channel_info = api.get_channel_info(for_username=channel_name)
        playlist_id = channel_info.items[0].contentDetails.relatedPlaylists.uploads
        playlist_items = api.get_playlist_items(playlist_id=playlist_id, count=None)

        # Prepare the output file path
        training_data_file = os.path.join('storage/cache', channel_name, 'training_data.jsonl')
        os.makedirs(os.path.dirname(training_data_file), exist_ok=True)

        # Open the output file
        with open(training_data_file, 'w', encoding='utf-8') as f:
            for item in playlist_items.items:
                video_id = item.contentDetails.videoId

                # Check if the transcript is cached
                cache_file_path = os.path.join('storage/cache', channel_name, f"{video_id}.json")
                if os.path.exists(cache_file_path):
                    with open(cache_file_path, 'r') as cache_file:
                        transcript_data = json.load(cache_file)
                        transcript = transcript_data['transcript']
                else:
                    # Fetch the transcript for the video if not cached
                    try:
                        transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
                        transcript = " ".join([t['text'] for t in transcript_list])
                    except Exception as e:
                        print(f"Could not fetch transcript for video ID {video_id}: {e}")
                        continue

                # Prepare the JSON object for fine-tuning
                data = {
                    "input": transcript,  # Transcript of the video
                    "output": None  # Placeholder for the response (e.g., summary)
                }

                # Write the JSON object as a new line in the output file
                f.write(json.dumps(data) + "\n")

        print(f"Fine-tuning data saved to {training_data_file}")

    except Exception as e:
        print(f"Error preparing fine-tuning data: {e}")
        return []

@app.route('/training_data', methods=['GET'])
def list_training_data():
    # Get the list of channels from the cache directory
    cache_dir = 'storage/cache'
    channels = []

    # Iterate through the cache directory to find training_data.jsonl files
    for channel_name in os.listdir(cache_dir):
        training_data_file = os.path.join(cache_dir, channel_name, 'training_data.jsonl')
        if os.path.isfile(training_data_file):
            channels.append(channel_name)

    return render_template('training_data.html', channels=channels)

@app.route('/sync_training_data', methods=['POST'])
def sync_training_data():
    cache_dir = 'storage/cache'
    try:
        # Check if the bucket exists, if not, create it
        if not bucket_exists(BUCKET_NAME):
            s3.create_bucket(Bucket=BUCKET_NAME, CreateBucketConfiguration={
                'LocationConstraint': 'us-west-2'})  # Specify the region

        # Iterate through the cache directory to find training_data.jsonl files
        for channel_name in os.listdir(cache_dir):
            print(f"channel_name {channel_name}")
            training_data_file = os.path.join(cache_dir, channel_name, 'training_data.jsonl')
            if os.path.isfile(training_data_file):
                # Upload the training data file to S3
                s3.upload_file(training_data_file, BUCKET_NAME, f"{channel_name}/training_data.jsonl")
        
        return "Training data synchronized successfully.", 200

    except (NoCredentialsError, PartialCredentialsError) as e:
        return f"Error with AWS credentials: {str(e)}", 403
    except Exception as e:
        return f"An error occurred: {str(e)}", 500

def bucket_exists(bucket_name):
    """Check if an S3 bucket exists."""
    try:
        s3.head_bucket(Bucket=bucket_name)
        return True
    except Exception as e:
        return False

@ns_training.route('/sync')
class SyncTrainingData(Resource):
    @api.response(200, 'Training data synchronized successfully')
    @api.response(403, 'Error with AWS credentials')
    @api.response(500, 'An error occurred')
    def post(self):
        """Synchronize training data to S3"""
        try:
            # ... (your existing sync_training_data logic)
            return "Training data synchronized successfully.", 200
        except (NoCredentialsError, PartialCredentialsError) as e:
            api.abort(403, f"Error with AWS credentials: {str(e)}")
        except Exception as e:
            api.abort(500, f"An error occurred: {str(e)}")

@ns_jobs.route('/<string:job_id>')
class Job(Resource):
    @api.doc(params={'job_id': 'Unique job identifier'})
    @api.response(200, 'Success', job_model)
    @api.response(404, 'Job not found')
    def get(self, job_id):
        """Get the status and results of a specific job"""
        if job_id not in jobs:
            api.abort(404, f"Job {job_id} not found")
        return jobs[job_id]

@ns_jobs.route('/')
class JobList(Resource):
    @api.doc(params={
        'page': {'type': 'int', 'default': 1, 'description': 'Page number'},
        'per_page': {'type': 'int', 'default': 10, 'description': 'Items per page'}
    })
    @api.response(200, 'Success', fields.List(fields.Nested(job_model)))
    def get(self):
        """List all jobs with pagination"""
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 10))
        start = (page - 1) * per_page
        end = start + per_page
        job_list = list(jobs.values())[start:end]
        return job_list

def extract_video_id(url):
    """Extract video ID from various forms of YouTube URLs"""
    parsed_url = urlparse(url)
    if parsed_url.hostname in ('www.youtube.com', 'youtube.com'):
        if parsed_url.path == '/watch':
            return parse_qs(parsed_url.query)['v'][0]
        elif parsed_url.path.startswith(('/embed/', '/v/')):
            return parsed_url.path.split('/')[2]
    elif parsed_url.hostname == 'youtu.be':
        return parsed_url.path[1:]
    return None

def translate_with_claude(text, target_language="English"):
    """Translate text using Claude with improved formatting and chunking"""
    try:
        # Validate input
        if not text or not isinstance(text, str):
            print(f"Invalid input text: {text}")
            return None
            
        print(f"Starting translation of text length: {len(text)}")  # Debug log
        
        # Use smaller chunks to stay within Claude's token limits
        CHUNK_SIZE = 8000
        chunks = []
        
        # Simple chunking by character count at sentence boundaries
        current_chunk = ""
        sentences = text.replace('\n', ' \n ').split('.')
        
        for sentence in sentences:
            sentence = sentence.strip() + '.'
            if len(current_chunk) + len(sentence) > CHUNK_SIZE:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = sentence
            else:
                current_chunk += ' ' + sentence if current_chunk else sentence
                
        if current_chunk:
            chunks.append(current_chunk)
            
        print(f"Split text into {len(chunks)} chunks")  # Debug log
        translated_chunks = []

        for i, chunk in enumerate(chunks):
            print(f"Translating chunk {i+1}/{len(chunks)} of length {len(chunk)}")  # Debug log
            try:
                message = anthropic.messages.create(
                    model="claude-3-sonnet-20240229",
                    max_tokens=4096,
                    temperature=0,
                    system="You are a translation API. Translate the input text to natural English, preserving structure and formatting. Format the text into logical paragraphs with proper spacing between dialogue and sections. Do not include any introductory text or explanations.",
                    messages=[
                        {
                            "role": "user",
                            "content": f"Translate this text to English and format it into clear paragraphs. Provide only the translation, no introductory text:\n\n{chunk}"
                        }
                    ]
                )
                
                # Extract and clean the translation
                if hasattr(message, 'content'):
                    if isinstance(message.content, list) and len(message.content) > 0:
                        chunk_translation = message.content[0].text
                    else:
                        chunk_translation = str(message.content)
                    
                    # Clean up the translation
                    chunk_translation = chunk_translation.strip()
                    
                    # Remove common introductory phrases
                    intro_phrases = [
                        "Here is the translation formatted into paragraphs:",
                        "Here's the translation:",
                        "Translated text:",
                        "Here is the English translation:",
                        "Translation:"
                    ]
                    
                    for phrase in intro_phrases:
                        if chunk_translation.startswith(phrase):
                            chunk_translation = chunk_translation[len(phrase):].strip()
                    
                    if chunk_translation:
                        translated_chunks.append(chunk_translation)
                        print(f"Successfully translated chunk {i+1}")  # Debug log
                    else:
                        print(f"Empty translation for chunk {i+1}")  # Debug log
                else:
                    print(f"Unexpected message format: {message}")  # Debug log

            except Exception as chunk_error:
                print(f"Error translating chunk {i+1}: {chunk_error}")  # Debug log
                continue

        if not translated_chunks:
            print("No successful translations")  # Debug log
            return None

        # Combine chunks with proper spacing
        full_translation = ""
        for chunk in translated_chunks:
            if full_translation:
                if not full_translation.endswith('\n') and not chunk.startswith('\n'):
                    full_translation += '\n\n'
                elif not full_translation.endswith('\n'):
                    full_translation += '\n'
            full_translation += chunk

        print(f"Final translation length: {len(full_translation)}")  # Debug log
        return full_translation

    except Exception as e:
        print(f"Translation error: {e}")
        return None

def detect_language_with_claude(text):
    """Use Claude to detect the language of the text"""
    try:
        message = anthropic.messages.create(
            model="claude-3-sonnet-20240229",
            max_tokens=100,
            temperature=0,
            system="You are a language detection API. Return only the language name in English.",
            messages=[
                {
                    "role": "user",
                    "content": f"""What language is this text written in? Respond with just the language name in English:

{text[:500]}"""  # Use first 500 chars for detection
                }
            ]
        )
        
        content = message.content[0].text if isinstance(message.content, list) else message.content
        return content.strip()
    except Exception as e:
        print(f"Language detection error: {e}")
        return "Unknown"

def fetch_single_transcript(video_url, translate=False):
    try:
        video_id = extract_video_id(video_url)
        if not video_id:
            return {"error": "Invalid YouTube URL"}, 400

        try:
            # Get video details first
            video_response = youtube.videos().list(
                part='snippet',
                id=video_id
            ).execute()
            
            if video_response['items']:
                video_info = video_response['items'][0]['snippet']
                title = video_info['title']
                author = video_info['channelTitle']
            else:
                title = "Unknown Title"
                author = "Unknown Author"

            cache_dir = os.path.join('storage', 'transcripts', video_id)
            os.makedirs(cache_dir, exist_ok=True)
            
            original_cache_path = os.path.join(cache_dir, 'original.txt')
            translated_cache_path = os.path.join(cache_dir, 'translated.txt')
            
            # Get transcript content
            if os.path.exists(original_cache_path):
                with open(original_cache_path, 'r', encoding='utf-8') as f:
                    original_text = f.read()
                    transcript_text = original_text.split('\n\n', 1)[1] if '\n\n' in original_text else original_text
                    original_language = 'cached'
            else:
                transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
                transcript = transcript_list.find_transcript(['en', 'es', 'fr', 'de', 'it', 'pt', 'ru', 'ja', 'ko', 'zh'])
                transcript_data = transcript.fetch()
                transcript_text = " ".join([t['text'] for t in transcript_data])
                original_language = transcript.language_code
                
                # Format original with header
                original_text = f"""Title: {title}
Author: {author}

{transcript_text}"""
                
                # Save original
                with open(original_cache_path, 'w', encoding='utf-8') as f:
                    f.write(original_text)
            
            # Handle translation if requested
            if translate and original_language != 'en':
                print(f"Translation requested. Original language: {original_language}")
                translated_text = None
                
                # Check if translation exists and needs updating
                if os.path.exists(translated_cache_path):
                    print("Checking cached translation")
                    with open(translated_cache_path, 'r', encoding='utf-8') as f:
                        translated_text = f.read()
                    
                    # Check if the cached translation needs updating
                    if not translated_text.startswith("Title:"):
                        print("Adding title to cached translation")
                        # Translate just the title
                        translated_title = translate_with_claude(title)
                        if translated_title:
                            # Prepend title and author to existing translation
                            translated_text = f"Title: {translated_title}\nAuthor: {author}\n\n{translated_text}"
                            # Update the cache file
                            with open(translated_cache_path, 'w', encoding='utf-8') as f:
                                f.write(translated_text)
                else:
                    print("Requesting new translation from Claude")
                    # Translate title and transcript separately
                    translated_title = translate_with_claude(title)
                    translated_transcript = translate_with_claude(transcript_text)
                    
                    if translated_title and translated_transcript:
                        translated_text = f"Title: {translated_title}\nAuthor: {author}\n\n{translated_transcript}"
                        with open(translated_cache_path, 'w', encoding='utf-8') as f:
                            f.write(translated_text)
                    else:
                        translated_text = None
            else:
                print(f"Translation not needed. translate_to_english: {translate}, original_language: {original_language}")
            
            # Create response with download URLs
            response = {
                "video_id": video_id,
                "title": title,
                "author": author,
                "transcript": original_text,
                "url": video_url,
                "original_language": original_language,
                "download_urls": {
                    "original": f"/download/{video_id}/original",
                }
            }
            
            if translated_text:
                response["translated_transcript"] = translated_text
                response["download_urls"]["translated"] = f"/download/{video_id}/translated"

            return response, 200

        except Exception as e:
            return {"error": f"Failed to fetch transcript: {str(e)}"}, 500

    except Exception as e:
        return {"error": str(e)}, 500

@app.route('/single_transcript', methods=['GET'])
def get_single_transcript():
    video_url = request.args.get('url')
    translate = request.args.get('translate', 'false').lower() == 'true'
    
    if not video_url:
        return jsonify({"error": "Please provide a video URL."}), 400

    result, status_code = fetch_single_transcript(video_url, translate)
    return jsonify(result), status_code

@app.route('/download/<video_id>/<type>', methods=['GET'])
def download_transcript(video_id, type):
    try:
        cache_dir = os.path.join('storage', 'transcripts', video_id)
        file_path = os.path.join(cache_dir, f'{type}.txt')
        
        if not os.path.exists(file_path):
            return jsonify({"error": "Transcript not found"}), 404
            
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        response = Response(content, mimetype='text/plain')
        response.headers['Content-Disposition'] = f'attachment; filename={video_id}_{type}_transcript.txt'
        return response
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/transcripts/list')
def list_transcripts():
    """List all stored transcripts"""
    try:
        transcripts = []
        storage_dir = os.path.join('storage', 'transcripts')
        
        if os.path.exists(storage_dir):
            for video_id in os.listdir(storage_dir):
                transcript_dir = os.path.join(storage_dir, video_id)
                if os.path.isdir(transcript_dir):
                    # Read original transcript metadata
                    original_path = os.path.join(transcript_dir, 'original.txt')
                    translated_path = os.path.join(transcript_dir, 'translated.txt')
                    
                    if os.path.exists(original_path):
                        # Get video details from YouTube API
                        video_response = youtube.videos().list(
                            part='snippet',
                            id=video_id
                        ).execute()
                        
                        if video_response['items']:
                            video_info = video_response['items'][0]['snippet']
                            title = video_info['title']
                            author = video_info['channelTitle']
                        else:
                            title = "Unknown Title"
                            author = "Unknown Author"
                            
                        transcripts.append({
                            'video_id': video_id,
                            'title': title,
                            'author': author,
                            'has_translation': os.path.exists(translated_path)
                        })
        
        return render_template('transcripts.html', transcripts=transcripts)
    except Exception as e:
        return render_template('transcripts.html', error=str(e))

@app.route('/transcripts/view/<video_id>')
def view_transcript(video_id):
    """View a specific transcript"""
    try:
        transcript_dir = os.path.join('storage', 'transcripts', video_id)
        original_path = os.path.join(transcript_dir, 'original.txt')
        translated_path = os.path.join(transcript_dir, 'translated.txt')
        original_audio_path = os.path.join(transcript_dir, 'original_audio.mp3')
        translated_audio_path = os.path.join(transcript_dir, 'translated_audio.mp3')
        
        if not os.path.exists(original_path):
            return render_template('transcript_view.html', 
                                 error="Transcript not found")
        
        # Read the transcript files
        with open(original_path, 'r', encoding='utf-8') as f:
            transcript_text = f.read()
            
        translated_text = None
        if os.path.exists(translated_path):
            with open(translated_path, 'r', encoding='utf-8') as f:
                translated_text = f.read()
        
        # Get video details
        video_response = youtube.videos().list(
            part='snippet',
            id=video_id
        ).execute()
        
        if video_response['items']:
            video_info = video_response['items'][0]['snippet']
            title = video_info['title']
            author = video_info['channelTitle']
        else:
            title = "Unknown Title"
            author = "Unknown Author"
            
        transcript_data = {
            'video_id': video_id,
            'title': title,
            'author': author,
            'transcript': transcript_text,
            'translated_transcript': translated_text,
            'original_language': 'Unknown',  # You might want to store this in a metadata file
            'has_original_audio': os.path.exists(original_audio_path),
            'has_translated_audio': os.path.exists(translated_audio_path)
        }
        
        return render_template('transcript_view.html', transcript=transcript_data)
    except Exception as e:
        return render_template('transcript_view.html', error=str(e))

@app.route('/generate_audio/<video_id>/<type>')
def generate_audio(video_id, type):
    try:
        transcript_dir = os.path.join('storage', 'transcripts', video_id)
        transcript_path = os.path.join(transcript_dir, f'{type}.txt')
        audio_path = os.path.join(transcript_dir, f'{type}_audio.mp3')
        
        # Check if regeneration is requested
        regenerate = request.args.get('regenerate', 'false').lower() == 'true'
        
        if os.path.exists(audio_path) and not regenerate:
            print(f"Serving cached audio for {video_id}")
            return send_file(
                audio_path,
                mimetype='audio/mpeg',
                as_attachment=False
            )
            
        if not os.path.exists(transcript_path):
            return jsonify({"error": "Transcript not found"}), 404
            
        with open(transcript_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # Extract just the transcript part (after the headers)
        text = content.split('\n\n', 1)[1] if '\n\n' in content else content
        
        # Count total paragraphs for progress calculation
        total_paragraphs = len([p for p in text.split('\n\n') if p.strip()])
        processed_paragraphs = 0
        
        print(f"Starting audio generation for {video_id} with {total_paragraphs} paragraphs")
        
        def text_stream():
            nonlocal processed_paragraphs
            paragraphs = text.split('\n\n')
            for paragraph in paragraphs:
                if paragraph.strip():
                    processed_paragraphs += 1
                    progress = (processed_paragraphs / total_paragraphs) * 100
                    print(f"Processing paragraph {processed_paragraphs}/{total_paragraphs} ({progress:.1f}%)")
                    yield paragraph.strip() + " "

        # Generate audio stream
        print(f"Generating new audio for {video_id}")
        audio_stream = client.generate(
            text=text_stream(),
            voice=voice_id,
            model="eleven_multilingual_v2",
            stream=True
        )

        # Save the audio file while streaming
        total_size = 0
        with open(audio_path, 'wb') as f:
            for chunk in audio_stream:
                f.write(chunk)
                total_size += len(chunk)
                print(f"Received audio chunk: {len(chunk)} bytes (Total: {total_size} bytes)")

        print(f"Audio generation complete for {video_id}. Total size: {total_size} bytes")
        
        # Return the saved file
        return send_file(
            audio_path,
            mimetype='audio/mpeg',
            as_attachment=False
        )
        
    except Exception as e:
        print(f"Audio generation error: {e}")  # Debug log
        return jsonify({"error": str(e)}), 500

@click.group()
def cli():
    """CLI for interacting with the Transcript Service."""
    pass

@cli.command()
@click.option('--channel_name', required=True, help='YouTube channel name to fetch transcripts from.')
@click.option('--author', help='YouTube author name to filter results.')
def fetch_transcripts(channel_name, author):
    """Fetch transcripts for a given YouTube channel."""
    result, status_code = fetch_transcripts(channel_name, author)
    click.echo(result)

@cli.command()
@click.argument('video_url')
@click.option('--translate', is_flag=True, default=False, help='Translate the transcript to English.')
def fetch_single_transcript(video_url, translate):
    """Fetch a single transcript for a given video URL."""
    result, status_code = fetch_single_transcript(video_url, translate)
    click.echo(result)

@cli.command()
@click.argument('job_id')
def check_job_status(job_id):
    """Check the status of a transcription job."""
    job_status = jobs.get(job_id)
    if job_status:
        click.echo(job_status)
    else:
        click.echo(f"Job {job_id} not found.")

if __name__ == '__main__':
    cli()