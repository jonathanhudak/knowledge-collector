# transcript_service.py
import requests
from pyyoutube import Client, Api
from dotenv import load_dotenv
from youtube_transcript_api import YouTubeTranscriptApi
import os
import json
import boto3
# YouTube Data API
import googleapiclient.discovery

# For displaying videos in Colab
from IPython.display import YouTubeVideo

load_dotenv()  

YOUTUBE_DATA_API_KEY = os.getenv('YOUTUBE_API_KEY')
api = Api(api_key=YOUTUBE_DATA_API_KEY)

youtube = googleapiclient.discovery.build(serviceName='youtube', version='v3', developerKey=YOUTUBE_DATA_API_KEY)

def search_yt(query):
    try:
        search_response = api.search_by_keywords(q=query, search_type="video", count=10)
        print(f"Search response: {search_response}")  # Debug print
        return search_response
    except Exception as e:
        print(f"Error in search_yt: {e}")
        return None

def display_yt_results(search_response):
    if not search_response or not search_response.items:
        print("No search results found")
        return None

    results = []
    for search_result in search_response.items:
        result = {
            'video_id': search_result.id.videoId,
            'title': search_result.snippet.title,
            'author': search_result.snippet.channelTitle,
            'url': f'https://www.youtube.com/watch?v={search_result.id.videoId}'
        }
        results.append(result)
        print(f'Video ID: {result["video_id"]}')
        print(f'Title: {result["title"]}')
        print(f'Author: {result["author"]}')
        print()
    return results

'''
Search Response JSON
https://developers.google.com/youtube/v3/docs/videos/list#response
{
    "kind": "youtube#searchListResponse",
    "etag": etag,
    "nextPageToken": string,
    "prevPageToken": string,
    "regionCode": string,
    "pageInfo": {
        "totalResults": integer,
        "resultsPerPage": integer
    },
    "items": [
        search Resource
    ]
}
'''
class Search_Response:
    def __init__(self, search_response) -> None:
        self.prev_page_token = search_response.get('prevPageToken')
        self.next_page_token = search_response.get('nextPageToken')

        # items element contain list of videos
        items = search_response.get('items')

        self.search_results = []
        for item in items:
            search_result = Search_Result(item)
            self.search_results.append(search_result)

'''
Search Results JSON
https://developers.google.com/youtube/v3/docs/search#resource
{
    "kind": "youtube#searchResult",
    "etag": etag,
    "id": {
        "kind": string,
        "videoId": string,
        "channelId": string,
        "playlistId": string
    },
    "snippet": {
        "publishedAt": datetime,
        "channelId": string,
        "title": string,
        "description": string,
        "thumbnails": {
          (key): {
              "url": string,
              "width": unsigned integer,
              "height": unsigned integer
          }
        },
        "channelTitle": string,
        "liveBroadcastContent": string
    }
}
'''
class Search_Result:
    def __init__(self, search_result) -> None:
        self.video_id=     search_result['id']['videoId']
        self.title=        search_result['snippet']['title']
        self.description=  search_result['snippet']['description']
        self.thumbnails=   search_result['snippet']['thumbnails']['default']['url']
