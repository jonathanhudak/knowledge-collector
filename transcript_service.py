# transcript_service.py
import requests
from flask import Flask, request, jsonify, render_template, Response
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

load_dotenv()  # Load environment variables
YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')

api = Api(api_key=YOUTUBE_API_KEY)

app = Flask(__name__)
api = Api(app, version='1.0', title='Transcript Service API',
          description='A simple API for managing transcripts and training data')

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
    author = request.args.get('author')  # Get channel_name from query parameters
    transcripts = fetch_transcripts(channel_name) if channel_name else []
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

if __name__ == '__main__':
    app.run(debug=True)