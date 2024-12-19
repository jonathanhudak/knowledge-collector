# Transcript Service

## Overview

The Transcript Service is a Flask-based web application that allows users to fetch, manage, and synchronize transcripts from YouTube videos. It integrates with various APIs, including YouTube Data API, YouTube Transcript API, and Eleven Labs for audio generation. The service supports features like fetching transcripts for a specific channel, translating transcripts, and generating audio from text.

## Features

- **Fetch Transcripts**: Retrieve transcripts for videos from a specified YouTube channel.
- **Translation**: Translate transcripts into English using the Claude API (off by default).
- **Audio Generation**: Generate audio files from transcripts using Eleven Labs.
- **Job Management**: Track the status of transcript fetching jobs.
- **S3 Synchronization**: Sync training data to an AWS S3 bucket.
- **Web Interface**: A user-friendly web interface to interact with the service.

## Setup

### Prerequisites

- Python 3.7 or higher
- Flask
- AWS account (for S3 synchronization)
- YouTube Data API key
- Anthropic API key (for translation)
- Eleven Labs API key (for audio generation)

### Installation

1. **Clone the repository**:

   ```bash
   git clone <repository-url>
   cd <repository-directory>
   ```

2. **Create a virtual environment** (optional but recommended):

   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows use `venv\Scripts\activate`
   ```

3. **Install dependencies**:

   ```bash
   pip install -r requirements.txt
   ```

4. **Set up environment variables**:
   Create a `.env` file in the root directory and add the following:

   ```plaintext
   YOUTUBE_API_KEY=<your_youtube_api_key>
   ANTHROPIC_API_KEY=<your_anthropic_api_key>
   XI_API_KEY=<your_eleven_labs_api_key>
   ```

5. **Run the application**:
   You can start the Flask application using the provided Makefile:
   ```bash
   make service
   ```

## Usage

### Fetching Transcripts

To fetch transcripts for a specific YouTube channel, you can use the following endpoint:

- **GET** `/transcripts?channel_name=<channel_name>&author=<author_name>`

### Viewing Transcripts

To view a specific transcript, navigate to:

- **GET** `/transcripts/view/<video_id>`

### Generating Audio

To generate audio from a transcript, use the following endpoint:

- **GET** `/generate_audio/<video_id>/<type>`

### Syncing Training Data

To synchronize training data to your S3 bucket, use the following endpoint:

- **POST** `/sync_training_data`

### Job Status

To check the status of a transcript fetching job, use:

- **GET** `/job_status/<job_id>`

### Fetching a Single Transcript

To fetch a single transcript for a given video URL, you can use the following endpoint:

- **GET** `/single_transcript?url=<video_url>&translate=<true|false>` (default is `false`)

### CLI Usage

The Transcript Service also provides a command-line interface (CLI) for interacting with the service. You can access the CLI commands by running:

```bash
make cli
```

### Available Commands

- **fetch-transcripts**: Fetch transcripts for a given YouTube channel.

  - Usage:
    ```bash
    python transcript_service.py fetch-transcripts --channel_name <channel_name> [--author <author_name>]
    ```

- **fetch-single-transcript**: Fetch a single transcript for a given video URL.

  - Usage:
    ```bash
    python transcript_service.py fetch-single-transcript <video_url> [--translate]
    ```

- **check-job-status**: Check the status of a transcription job.
  - Usage:
    ```bash
    python transcript_service.py check-job-status <job_id>
    ```

### Web Interface

The application provides a web interface that can be accessed at `http://localhost:5000`. You can use this interface to interact with the various features of the service.

## Contributing

Contributions are welcome! Please feel free to submit a pull request or open an issue for any enhancements or bug fixes.

## License

This project is licensed under the MIT License. See the LICENSE file for details.
