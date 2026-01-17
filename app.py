import os, re, tempfile, uuid
from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
import yt_dlp

app = Flask(__name__, static_folder='static')
CORS(app)

YOUTUBE_CHANNEL_ID = "UCKnu9e0Rk4BDQrt22sT1KpA"
MAX_CLIP_DURATION = 120
TEMP_DIR = tempfile.gettempdir()

def parse_timestamp(ts):
    parts = ts.strip().split(':')
    if len(parts) == 2: return int(parts[0]) * 60 + int(parts[1])
    elif len(parts) == 3: return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    raise ValueError(f"Invalid timestamp: {ts}")

def validate_youtube_url(url):
    match = re.search(r'(?:youtube\.com\/watch\?v=|youtu\.be\/)([a-zA-Z0-9_-]{11})', url)
    if match: return match.group(1)
    raise ValueError("Invalid YouTube URL")

@app.route('/')
def serve_index():
    return send_from_directory('static', 'index.html')

@app.route('/api/episodes', methods=['GET'])
def get_episodes():
    try:
        ydl_opts = {'extract_flat': True, 'quiet': True, 'no_warnings': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(f"https://www.youtube.com/channel/{YOUTUBE_CHANNEL_ID}/videos", download=False)
        episodes = []
        for entry in (result.get('entries') or [])[:50]:
            if entry:
                episodes.append({'id': entry.get('id'), 'title': entry.get('title'), 'url': f"https://www.youtube.com/watch?v={entry.get('id')}", 'thumbnail': f"https://img.youtube.com/vi/{entry.get('id')}/mqdefault.jpg", 'duration': entry.get('duration')})
        return jsonify({'episodes': episodes})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/clip', methods=['POST'])
def generate_clip():
    output_path = None
    try:
        data = request.get_json()
        youtube_url, start_time, end_time = data.get('youtube_url'), data.get('start_time'), data.get('end_time')
        if not all([youtube_url, start_time, end_time]): return jsonify({'error': 'Missing fields'}), 400
        start_sec, end_sec = parse_timestamp(start_time), parse_timestamp(end_time)
        if end_sec <= start_sec: return jsonify({'error': 'End must be after start'}), 400
        if end_sec - start_sec > MAX_CLIP_DURATION: return jsonify({'error': 'Max 2 minutes'}), 400
        validate_youtube_url(youtube_url)
        output_path = os.path.join(TEMP_DIR, f"tokenized_clip_{uuid.uuid4().hex[:8]}.mp4")
        ydl_opts = {'format': 'best[ext=mp4]/best', 'outtmpl': output_path, 'download_ranges': lambda i, y: [{'start_time': start_sec, 'end_time': end_sec}], 'force_keyframes_at_cuts': True, 'quiet': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl: ydl.download([youtube_url])
        if not os.path.exists(output_path): return jsonify({'error': 'Failed'}), 500
        return send_file(output_path, as_attachment=True, download_name=os.path.basename(output_path), mimetype='video/mp4')
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if output_path and os.path.exists(output_path):
            try: os.remove(output_path)
            except: pass

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=True)
