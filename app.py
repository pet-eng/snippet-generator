import os, re, tempfile, uuid, requests
from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
import subprocess

app = Flask(__name__, static_folder='static')
CORS(app)

MAX_CLIP_DURATION = 120
TEMP_DIR = tempfile.gettempdir()

# Hardcoded episodes - add new episodes here
# Format: {'title': 'Episode Title', 'file_id': 'Google Drive file ID'}
EPISODES = [
    {'title': 'Ep. 66', 'file_id': '1WRjWlHQHYR2GsIvTp5PgZ8xH53Ldz-_2', 'is_folder': True},
    {'title': 'Ep. 65', 'file_id': '1EaqVsa7AzZCml4t-UXmdIfA85AX9Zj3I', 'is_folder': False},
    {'title': 'Ep. 64', 'file_id': '1_T1IJawnFPfoAsy4gfcODanBTfm2br2a', 'is_folder': False},
    {'title': 'Ep. 63', 'file_id': '1ElAuxl7_PjgpWzZDIoIqBRchjPNZ4HVr', 'is_folder': False},
    {'title': 'Ep. 62', 'file_id': '19Hq49X8zyfySIa3gTn0maOPW50As-wUa', 'is_folder': False},
    {'title': 'Ep. 61', 'file_id': '1Vy0OdQb1oPc81FlZVMpz_iisA3nqYISo', 'is_folder': False},
]

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

def get_mp4_from_folder(folder_id):
    """Get the first MP4 file from a Google Drive folder."""
    url = f"https://drive.google.com/embeddedfolderview?id={folder_id}"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    response = requests.get(url, headers=headers)

    patterns = [
        r'/file/d/([a-zA-Z0-9_-]+)[^>]*>([^<]*\.mp4)',
        r'data-id="([a-zA-Z0-9_-]+)"[^>]*>([^<]*\.mp4)',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, response.text, re.IGNORECASE)
        if matches:
            return matches[0][0]
    return None

def download_gdrive_file(file_id, output_path):
    """Download a file from Google Drive."""
    url = f"https://drive.google.com/uc?export=download&id={file_id}&confirm=t"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    session = requests.Session()
    response = session.get(url, headers=headers, stream=True)

    for key, value in response.cookies.items():
        if key.startswith('download_warning'):
            url = f"https://drive.google.com/uc?export=download&id={file_id}&confirm={value}"
            response = session.get(url, headers=headers, stream=True)
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
        episodes = []
        for i, ep in enumerate(EPISODES):
            ep_match = re.search(r'[Ee]p\.?\s*(\d+)', ep['title'])
            ep_num = int(ep_match.group(1)) if ep_match else i

            episodes.append({
                'id': ep['file_id'],
                'title': ep['title'],
                'episode_num': ep_num,
                'is_folder': ep.get('is_folder', False)
            })

        return jsonify({'episodes': episodes})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/clip', methods=['POST'])
def generate_clip():
    video_path = None
    output_path = None
    try:
        data = request.get_json()
        file_id = data.get('file_id')
        is_folder = data.get('is_folder', False)
        start_time = data.get('start_time')
        end_time = data.get('end_time')

        if not all([file_id, start_time, end_time]):
            return jsonify({'error': 'Missing fields'}), 400

        start_sec = parse_timestamp(start_time)
        end_sec = parse_timestamp(end_time)

        if end_sec <= start_sec:
            return jsonify({'error': 'End must be after start'}), 400
        if end_sec - start_sec > MAX_CLIP_DURATION:
            return jsonify({'error': 'Max 2 minutes'}), 400

        if is_folder:
            mp4_file_id = get_mp4_from_folder(file_id)
            if not mp4_file_id:
                return jsonify({'error': 'No MP4 file found in episode folder'}), 404
        else:
            mp4_file_id = file_id

        video_path = os.path.join(TEMP_DIR, f"source_{uuid.uuid4().hex[:8]}.mp4")
        if not download_gdrive_file(mp4_file_id, video_path):
            return jsonify({'error': 'Failed to download video'}), 500

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
        for path in [video_path, output_path]:
            if path and os.path.exists(path):
                try: os.remove(path)
                except: pass

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=True)
