import os, re, tempfile, uuid, requests
from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
import subprocess

app = Flask(__name__, static_folder='static')
CORS(app)

# Google Drive folder ID (from the shared link)
GDRIVE_FOLDER_ID = "1cTr1l6rYELx8VQKAvFoc6gP7t3MBZQZh"
MAX_CLIP_DURATION = 120
TEMP_DIR = tempfile.gettempdir()

def parse_timestamp(ts):
    parts = ts.strip().split(':')
    if len(parts) == 2: return int(parts[0]) * 60 + int(parts[1])
    elif len(parts) == 3: return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    raise ValueError(f"Invalid timestamp: {ts}")

def format_timestamp(seconds):
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"

def get_gdrive_folders(folder_id):
    """Get list of subfolders from a public Google Drive folder."""
    url = f"https://drive.google.com/embeddedfolderview?id={folder_id}"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    response = requests.get(url, headers=headers)

    # Parse folder names and IDs from the HTML response
    folders = []
    # Match pattern for folder entries
    import re
    pattern = r'href="https://drive\.google\.com/drive/folders/([^"]+)"[^>]*>([^<]+)</a>'
    matches = re.findall(pattern, response.text)

    for folder_id, folder_name in matches:
        folders.append({'id': folder_id, 'name': folder_name.strip()})

    return folders

def get_mp4_from_folder(folder_id):
    """Get the first MP4 file from a Google Drive folder."""
    url = f"https://drive.google.com/embeddedfolderview?id={folder_id}"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    response = requests.get(url, headers=headers)

    # Match pattern for file entries (MP4)
    pattern = r'href="https://drive\.google\.com/file/d/([^/]+)/[^"]*"[^>]*>([^<]*\.mp4)</a>'
    matches = re.findall(pattern, response.text, re.IGNORECASE)

    if matches:
        return {'id': matches[0][0], 'name': matches[0][1]}
    return None

def download_gdrive_file(file_id, output_path):
    """Download a file from Google Drive."""
    # Use the direct download URL for Google Drive
    url = f"https://drive.google.com/uc?export=download&id={file_id}&confirm=t"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    response = requests.get(url, headers=headers, stream=True)

    # Handle large file warning
    if 'download_warning' in response.text or len(response.content) < 100000:
        # Try to get the confirm token
        for key, value in response.cookies.items():
            if key.startswith('download_warning'):
                url = f"https://drive.google.com/uc?export=download&id={file_id}&confirm={value}"
                response = requests.get(url, headers=headers, stream=True)
                break

    with open(output_path, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

    return os.path.exists(output_path) and os.path.getsize(output_path) > 0

@app.route('/')
def serve_index():
    return send_from_directory('static', 'index.html')

@app.route('/api/episodes', methods=['GET'])
def get_episodes():
    try:
        folders = get_gdrive_folders(GDRIVE_FOLDER_ID)

        episodes = []
        for folder in folders:
            name = folder['name']
            # Extract episode number if present
            ep_match = re.search(r'[Ee]p\.?\s*(\d+)', name)
            ep_num = int(ep_match.group(1)) if ep_match else 0

            episodes.append({
                'id': folder['id'],
                'title': name,
                'episode_num': ep_num,
                'thumbnail': f"https://drive.google.com/thumbnail?id={folder['id']}&sz=w320"
            })

        # Sort by episode number descending (newest first)
        episodes.sort(key=lambda x: x['episode_num'], reverse=True)

        return jsonify({'episodes': episodes})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/clip', methods=['POST'])
def generate_clip():
    video_path = None
    output_path = None
    try:
        data = request.get_json()
        folder_id = data.get('folder_id')
        start_time = data.get('start_time')
        end_time = data.get('end_time')

        if not all([folder_id, start_time, end_time]):
            return jsonify({'error': 'Missing fields'}), 400

        start_sec = parse_timestamp(start_time)
        end_sec = parse_timestamp(end_time)

        if end_sec <= start_sec:
            return jsonify({'error': 'End must be after start'}), 400
        if end_sec - start_sec > MAX_CLIP_DURATION:
            return jsonify({'error': 'Max 2 minutes'}), 400

        # Get the MP4 file from the folder
        mp4_file = get_mp4_from_folder(folder_id)
        if not mp4_file:
            return jsonify({'error': 'No MP4 file found in episode folder'}), 404

        # Download the video
        video_path = os.path.join(TEMP_DIR, f"source_{uuid.uuid4().hex[:8]}.mp4")
        if not download_gdrive_file(mp4_file['id'], video_path):
            return jsonify({'error': 'Failed to download video'}), 500

        # Cut the clip using FFmpeg
        output_path = os.path.join(TEMP_DIR, f"tokenized_clip_{uuid.uuid4().hex[:8]}.mp4")

        cmd = [
            'ffmpeg', '-y',
            '-ss', format_timestamp(start_sec),
            '-i', video_path,
            '-t', str(end_sec - start_sec),
            '-c:v', 'libx264', '-c:a', 'aac',
            '-preset', 'fast',
            output_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            return jsonify({'error': f'FFmpeg failed: {result.stderr[:500]}'}), 500

        return send_file(output_path, as_attachment=True, download_name=os.path.basename(output_path), mimetype='video/mp4')

    except Exception as e:
        return jsonify({'error': str(e)}), 500

    finally:
        # Clean up temp files
        for path in [video_path, output_path]:
            if path and os.path.exists(path):
                try: os.remove(path)
                except: pass

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=True)
