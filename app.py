import os
import threading
import time
import json
from datetime import datetime
from flask import Flask, request, render_template, send_from_directory, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit
import cv2
import ffmpeg
import uuid
import numpy as np
import redis

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
socketio = SocketIO(app, cors_allowed_origins="*")

# Redis connection for job management
try:
    redis_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
    redis_client.ping()  # Test connection
    REDIS_AVAILABLE = True
except:
    print("Redis not available, using in-memory job storage")
    REDIS_AVAILABLE = False
    job_storage = {}  # Fallback to in-memory storage

# Global dictionary to track active processing threads and their cancellation flags
active_jobs = {}  # job_id -> {'thread': thread_obj, 'should_cancel': threading.Event()}

def cleanup_temp_files(temp_files):
    """Clean up temporary files"""
    for file_path in temp_files:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            print(f"Error cleaning up {file_path}: {e}")

def cleanup_old_videos(max_files=3):
    """Clean up old videos, keeping only the most recent ones"""
    try:
        # Video file extensions to clean up
        video_extensions = {'.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.wmv'}
        
        # Get list of currently active job filenames to avoid deleting them
        active_filenames = set()
        for job_id, job_info in active_jobs.items():
            job_data = get_job_status(job_id)
            if job_data and job_data.get('status') in ['queued', 'processing']:
                # Extract base filename from job
                filename = job_data.get('filename', '')
                if filename:
                    active_filenames.add(filename)
                    # Also protect files with job_id in the name
                    active_filenames.add(f"{job_id}_{filename}")
        
        # Clean up both folders
        for folder in [UPLOAD_FOLDER, PROCESSED_FOLDER]:
            if not os.path.exists(folder):
                continue
                
            folder_name = os.path.basename(folder)
            print(f"Cleaning up {folder_name} folder...")
                
            # Get all video files with their modification times
            files_with_times = []
            for filename in os.listdir(folder):
                if filename.startswith('.'):  # Skip hidden files
                    continue
                
                # Only process video files
                file_ext = os.path.splitext(filename)[1].lower()
                if file_ext not in video_extensions:
                    continue
                    
                file_path = os.path.join(folder, filename)
                if os.path.isfile(file_path):
                    # Check if file is from an active job
                    is_active = False
                    for active_name in active_filenames:
                        if active_name in filename or filename in active_name:
                            is_active = True
                            break
                    
                    if not is_active:
                        mod_time = os.path.getmtime(file_path)
                        files_with_times.append((file_path, mod_time, filename))
            
            # Sort by modification time (newest first)
            files_with_times.sort(key=lambda x: x[1], reverse=True)
            
            # Keep only the max_files most recent, delete the rest
            files_to_delete = files_with_times[max_files:]
            
            deleted_count = 0
            for file_path, mod_time, filename in files_to_delete:
                try:
                    file_size = os.path.getsize(file_path)
                    os.remove(file_path)
                    print(f"🗑️ Deleted old video: {filename} ({file_size / (1024*1024):.1f} MB)")
                    deleted_count += 1
                except Exception as e:
                    print(f"❌ Error deleting {filename}: {e}")
            
            if deleted_count > 0:
                remaining_count = len(files_with_times) - deleted_count
                print(f"✅ Cleaned up {deleted_count} old videos from {folder_name}, keeping {remaining_count} recent files")
            else:
                print(f"📁 No cleanup needed in {folder_name} folder")
                
    except Exception as e:
        print(f"❌ Error during cleanup: {e}")

UPLOAD_FOLDER = 'static/uploads'
PROCESSED_FOLDER = 'static/processed'
ALLOWED_EXTENSIONS = {'mp4', 'avi', 'mov', 'mkv'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['PROCESSED_FOLDER'] = PROCESSED_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100 MB max upload size
app.config['MAX_STORED_VIDEOS'] = 3  # Maximum number of videos to keep in each folder

# Ensure the upload and processed directories exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(PROCESSED_FOLDER, exist_ok=True)

# Clean up old videos on startup
print("Performing startup cleanup...")
cleanup_old_videos(max_files=app.config['MAX_STORED_VIDEOS'])

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Job Management Functions
def create_job(job_id, filename, options):
    """Create a new processing job"""
    job_data = {
        'id': job_id,
        'filename': filename,
        'options': options,
        'status': 'queued',
        'progress': 0,
        'message': 'Job queued for processing',
        'created_at': datetime.now().isoformat(),
        'output_filename': None,
        'error': None
    }
    
    if REDIS_AVAILABLE:
        redis_client.hset(f"job:{job_id}", mapping=job_data)
    else:
        job_storage[job_id] = job_data
    
    return job_data

def update_job_progress(job_id, progress, message, status=None):
    """Update job progress and emit to connected clients"""
    if REDIS_AVAILABLE:
        job_data = redis_client.hgetall(f"job:{job_id}")
        job_data['progress'] = progress
        job_data['message'] = message
        if status:
            job_data['status'] = status
        redis_client.hset(f"job:{job_id}", mapping=job_data)
    else:
        if job_id in job_storage:
            job_storage[job_id]['progress'] = progress
            job_storage[job_id]['message'] = message
            if status:
                job_storage[job_id]['status'] = status
    
    # Emit progress to connected clients
    socketio.emit('progress_update', {
        'job_id': job_id,
        'progress': progress,
        'message': message,
        'status': status or 'processing'
    })

def get_job_status(job_id):
    """Get current job status"""
    if REDIS_AVAILABLE:
        job_data = redis_client.hgetall(f"job:{job_id}")
        return dict(job_data) if job_data else None
    else:
        return job_storage.get(job_id)

def complete_job(job_id, output_filename=None, error=None):
    """Mark job as completed or failed"""
    status = 'completed' if output_filename else 'failed'
    
    if REDIS_AVAILABLE:
        job_data = redis_client.hgetall(f"job:{job_id}")
        job_data['status'] = status
        job_data['progress'] = 100 if output_filename else 0
        job_data['output_filename'] = output_filename
        job_data['error'] = error
        job_data['completed_at'] = datetime.now().isoformat()
        redis_client.hset(f"job:{job_id}", mapping=job_data)
    else:
        if job_id in job_storage:
            job_storage[job_id]['status'] = status
            job_storage[job_id]['progress'] = 100 if output_filename else 0
            job_storage[job_id]['output_filename'] = output_filename
            job_storage[job_id]['error'] = error
            job_storage[job_id]['completed_at'] = datetime.now().isoformat()
    
    # Clean up active job tracking
    if job_id in active_jobs:
        del active_jobs[job_id]
    
    # Emit completion status
    socketio.emit('job_completed', {
        'job_id': job_id,
        'status': status,
        'output_filename': output_filename,
        'error': error
    })

def cancel_job(job_id):
    """Cancel a running job"""
    if job_id in active_jobs:
        # Signal the thread to stop
        active_jobs[job_id]['should_cancel'].set()
        
        # Update job status
        if REDIS_AVAILABLE:
            job_data = redis_client.hgetall(f"job:{job_id}")
            job_data['status'] = 'cancelled'
            job_data['progress'] = 0
            job_data['message'] = 'Job cancelled by user'
            job_data['error'] = 'Processing cancelled by user'
            job_data['completed_at'] = datetime.now().isoformat()
            redis_client.hset(f"job:{job_id}", mapping=job_data)
        else:
            if job_id in job_storage:
                job_storage[job_id]['status'] = 'cancelled'
                job_storage[job_id]['progress'] = 0
                job_storage[job_id]['message'] = 'Job cancelled by user'
                job_storage[job_id]['error'] = 'Processing cancelled by user'
                job_storage[job_id]['completed_at'] = datetime.now().isoformat()
        
        # Emit cancellation status
        socketio.emit('job_completed', {
            'job_id': job_id,
            'status': 'cancelled',
            'output_filename': None,
            'error': 'Processing cancelled by user'
        })
        
        return True
    return False

def process_video_background(job_id, input_path, output_path, options, original_filename):
    """Background video processing function with progress updates"""
    # Get cancellation event for this job
    should_cancel = active_jobs.get(job_id, {}).get('should_cancel')
    if not should_cancel:
        complete_job(job_id, error="Job cancellation system not initialized")
        return
    
    # List to track temporary files for cleanup
    temp_files = []
    
    try:
        # Check for cancellation before starting
        if should_cancel.is_set():
            cleanup_temp_files(temp_files)
            return
            
        update_job_progress(job_id, 5, "Starting video processing...")
        
        transformation = options.get('transformation', 'none')
        selected_filter = options.get('filter', 'none')
        speed = float(options.get('speed', '1.0'))
        brightness_str = options.get('brightness', '1.0')
        contrast_str = options.get('contrast', '1.0')
        
        unique_id = job_id
        
        # Check for cancellation
        if should_cancel.is_set():
            cleanup_temp_files(temp_files)
            return
            
        update_job_progress(job_id, 10, "Initializing FFmpeg stream...")
        
        stream = ffmpeg.input(input_path)
        ffmpeg_filters_applied_this_pass = False

        # Apply transformations
        if transformation == 'grayscale':
            stream = stream.filter('format', pix_fmts='gray')
            ffmpeg_filters_applied_this_pass = True
        elif transformation == 'invert':
            stream = stream.filter('negate')
            ffmpeg_filters_applied_this_pass = True
        elif transformation == 'hflip':
            stream = stream.hflip()
            ffmpeg_filters_applied_this_pass = True
        elif transformation == 'vflip':
            stream = stream.vflip()
            ffmpeg_filters_applied_this_pass = True
        elif transformation == 'rotate90':
            stream = stream.filter('rotate', 'PI/2')
            ffmpeg_filters_applied_this_pass = True
        elif transformation == 'rotate180':
            stream = stream.filter('rotate', 'PI')
            ffmpeg_filters_applied_this_pass = True
        elif transformation == 'rotate270':
            stream = stream.filter('rotate', '3*PI/2')
            ffmpeg_filters_applied_this_pass = True
        
        update_job_progress(job_id, 20, "Applying color adjustments...")
        
        # Apply brightness/contrast
        eq_params = {}
        try:
            b_val = float(brightness_str)
            if b_val != 1.0:
                eq_params['brightness'] = b_val - 1.0
        except ValueError:
            pass
        try:
            c_val = float(contrast_str)
            if c_val != 1.0:
                eq_params['contrast'] = c_val
        except ValueError:
            pass

        if eq_params:
            stream = stream.filter('eq', **eq_params)
            ffmpeg_filters_applied_this_pass = True

        # Speed adjustment for non-OpenCV filters
        if selected_filter == 'none' and speed != 1.0:
            stream = stream.setpts(f'{1/speed}*PTS')
            ffmpeg_filters_applied_this_pass = True
        
        update_job_progress(job_id, 30, "Processing video filters...")
        
        # Handle OpenCV filters with progress tracking
        if selected_filter != 'none':
            opencv_input_path = None
            audio_source_path_for_final_ffmpeg = None
            temp_ffmpeg_output = None

            if ffmpeg_filters_applied_this_pass:
                update_job_progress(job_id, 35, "Applying FFmpeg transformations...")
                temp_ffmpeg_output_filename = "temp_ffmpeg_" + original_filename + "_" + unique_id + ".mp4"
                temp_ffmpeg_output = os.path.join(app.config['PROCESSED_FOLDER'], temp_ffmpeg_output_filename)
                temp_files.append(temp_ffmpeg_output)
                
                very_temp_video_only_filename = f"very_temp_v_{unique_id}.mkv"
                very_temp_video_only_path = os.path.join(app.config['PROCESSED_FOLDER'], very_temp_video_only_filename)
                temp_files.append(very_temp_video_only_path)
                
                try:
                    stream.output(very_temp_video_only_path, vcodec='libx264', an=None)\
                               .run(overwrite_output=True, capture_stdout=True, capture_stderr=True)
                    
                    update_job_progress(job_id, 40, "Muxing audio and video...")
                    
                    try:
                        video_input_for_mux = ffmpeg.input(very_temp_video_only_path)
                        audio_input_for_mux = ffmpeg.input(input_path)
                        ffmpeg.output(video_input_for_mux.video, audio_input_for_mux.audio,
                                      temp_ffmpeg_output,
                                      vcodec='copy', acodec='aac', strict='experimental', shortest=None
                                     ).run(overwrite_output=True, capture_stdout=True, capture_stderr=True)
                        opencv_input_path = temp_ffmpeg_output
                        audio_source_path_for_final_ffmpeg = temp_ffmpeg_output
                    except ffmpeg.Error as e_mux:
                        error_str_mux = e_mux.stderr.decode('utf8')
                        if "Stream map 'a' matches no streams" in error_str_mux or "Cannot select audio stream" in error_str_mux:
                            ffmpeg.input(very_temp_video_only_path).output(temp_ffmpeg_output, vcodec='copy', an=None).run(overwrite_output=True, capture_stdout=True, capture_stderr=True)
                            opencv_input_path = temp_ffmpeg_output
                            audio_source_path_for_final_ffmpeg = None
                        else:
                            raise
                    finally:
                        if os.path.exists(very_temp_video_only_path):
                            os.remove(very_temp_video_only_path)
                except ffmpeg.Error as e:
                    complete_job(job_id, error=f"FFmpeg processing error: {e.stderr.decode('utf8')}")
                    return
            else:
                opencv_input_path = input_path
                audio_source_path_for_final_ffmpeg = input_path
            
            update_job_progress(job_id, 45, "Starting OpenCV processing...")
            
            # OpenCV processing with frame-level progress
            cap = cv2.VideoCapture(opencv_input_path)
            if not cap.isOpened():
                complete_job(job_id, error=f"Could not open video file: {opencv_input_path}")
                return

            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            fps = cap.get(cv2.CAP_PROP_FPS)
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            
            if fps == 0:
                fps = 25.0

            temp_opencv_filename = "temp_opencv_" + original_filename + "_" + unique_id + ".avi"
            temp_opencv_output_path = os.path.join(app.config['PROCESSED_FOLDER'], temp_opencv_filename)
            temp_files.append(temp_opencv_output_path)
            
            fourcc_cv = cv2.VideoWriter_fourcc(*'XVID')
            out_cv = cv2.VideoWriter(temp_opencv_output_path, fourcc_cv, fps, (width, height))
            
            if not out_cv.isOpened():
                cap.release()
                complete_job(job_id, error="OpenCV VideoWriter failed to open")
                return

            frame_count = 0
            processed_frames_written = 0
            
            while cap.isOpened():
                # Check for cancellation before processing each frame
                if should_cancel.is_set():
                    cap.release()
                    out_cv.release()
                    cleanup_temp_files(temp_files)
                    return
                    
                ret, frame = cap.read()
                if not ret:
                    break
                
                frame_count += 1
                if frame is None:
                    continue
                
                # Apply OpenCV filter
                processed_frame = frame
                if selected_filter == 'blur':
                    processed_frame = cv2.GaussianBlur(frame, (15, 15), 0)
                elif selected_filter == 'sharpen':
                    kernel = np.array([[-1,-1,-1], [-1,9,-1], [-1,-1,-1]])
                    processed_frame = cv2.filter2D(frame, -1, kernel)
                elif selected_filter == 'edge_detect':
                    gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    processed_frame_gray = cv2.Canny(gray_frame, 100, 200)
                    processed_frame = cv2.cvtColor(processed_frame_gray, cv2.COLOR_GRAY2BGR)
                
                if processed_frame is not None:
                    out_cv.write(processed_frame)
                    processed_frames_written += 1
                
                # Update progress every 30 frames
                if frame_count % 30 == 0 and total_frames > 0:
                    progress = 45 + int((frame_count / total_frames) * 30)  # 45-75% for OpenCV processing
                    update_job_progress(job_id, progress, f"Processing frame {frame_count}/{total_frames}")

            cap.release()
            out_cv.release()
            
            update_job_progress(job_id, 75, "Finalizing video encoding...")
            
            # Final FFmpeg re-encoding
            if os.path.exists(temp_opencv_output_path) and os.path.getsize(temp_opencv_output_path) > 0:
                try:
                    input_video_stream = ffmpeg.input(temp_opencv_output_path, r=str(fps))
                    output_streams = [input_video_stream.video]
                    
                    output_options = {
                        'vcodec': 'libx264',
                        'r': str(fps),
                        'video_bitrate': '1500k'
                    }
                    
                    if audio_source_path_for_final_ffmpeg:
                        try:
                            input_audio_stream = ffmpeg.input(audio_source_path_for_final_ffmpeg).audio
                            output_streams.append(input_audio_stream)
                            output_options['acodec'] = 'aac'
                            output_options['strict'] = 'experimental'
                        except:
                            pass
                    
                    ffmpeg.output(*output_streams, output_path, **output_options).run(overwrite_output=True, capture_stdout=True, capture_stderr=True)
                    
                    # Clean up temporary files
                    if os.path.exists(temp_opencv_output_path):
                        os.remove(temp_opencv_output_path)
                    if temp_ffmpeg_output and os.path.exists(temp_ffmpeg_output):
                        os.remove(temp_ffmpeg_output)
                    
                except ffmpeg.Error as e:
                    complete_job(job_id, error=f"Final encoding error: {e.stderr.decode('utf8')}")
                    return
            else:
                complete_job(job_id, error="OpenCV processing failed to produce valid output")
                return

            # Handle speed adjustment for OpenCV processed videos
            if speed != 1.0:
                update_job_progress(job_id, 85, f"Applying speed adjustment ({speed}x)...")
                speed_adjusted_input_path = output_path
                final_speed_adjusted_filename = "speed_adj_" + os.path.basename(output_path)
                final_speed_adjusted_path = os.path.join(app.config['PROCESSED_FOLDER'], final_speed_adjusted_filename)
                
                try:
                    speed_adj_stream = ffmpeg.input(speed_adjusted_input_path).setpts(f'{1/speed}*PTS')
                    speed_adj_stream.output(final_speed_adjusted_path, vcodec='libx264', acodec='aac', strict='experimental').run(overwrite_output=True, capture_stdout=True, capture_stderr=True)
                    
                    if os.path.exists(speed_adjusted_input_path) and speed_adjusted_input_path != final_speed_adjusted_path:
                        os.remove(speed_adjusted_input_path)
                    
                    output_path = final_speed_adjusted_path
                    output_filename = final_speed_adjusted_filename
                except ffmpeg.Error as e:
                    complete_job(job_id, error=f"Speed adjustment error: {e.stderr.decode('utf8')}")
                    return

        else:
            # Direct FFmpeg processing
            if ffmpeg_filters_applied_this_pass:
                update_job_progress(job_id, 50, "Processing with FFmpeg...")
                try:
                    stream.output(output_path, vcodec='libx264', acodec='aac', strict='experimental').run(overwrite_output=True, capture_stdout=True, capture_stderr=True)
                except ffmpeg.Error as e:
                    complete_job(job_id, error=f"FFmpeg processing error: {e.stderr.decode('utf8')}")
                    return
            else:
                update_job_progress(job_id, 80, "Copying video file...")
                try:
                    ffmpeg.input(input_path).output(output_path, vcodec='copy', acodec='copy').run(overwrite_output=True, capture_stdout=True, capture_stderr=True)
                except ffmpeg.Error as e:
                    complete_job(job_id, error=f"Video copy error: {e.stderr.decode('utf8')}")
                    return

        update_job_progress(job_id, 95, "Finalizing...")
        
        # Verify output file
        if not os.path.exists(output_path):
            complete_job(job_id, error="Processed file not found")
            return
        
        if os.path.getsize(output_path) == 0:
            complete_job(job_id, error="Processed file is empty")
            return
        
        # Final cancellation check
        if should_cancel.is_set():
            cleanup_temp_files(temp_files)
            return
            
        output_filename = os.path.basename(output_path)
        update_job_progress(job_id, 100, "Processing completed successfully!", "completed")
        complete_job(job_id, output_filename=output_filename)
        
        # Clean up old videos after successful completion
        print(f"Job {job_id} completed successfully, cleaning up old videos...")
        cleanup_old_videos(max_files=app.config['MAX_STORED_VIDEOS'])
        
    except Exception as e:
        # Clean up temp files on error
        cleanup_temp_files(temp_files)
        complete_job(job_id, error=f"Unexpected error: {str(e)}")

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        if 'file' not in request.files:
            return redirect(request.url)
        file = request.files['file']
        if file.filename == '':
            return redirect(request.url)
        if file and allowed_file(file.filename):
            original_filename = file.filename
            job_id = str(uuid.uuid4())
            input_filename = job_id + "_" + original_filename
            input_path = os.path.join(app.config['UPLOAD_FOLDER'], input_filename)
            file.save(input_path)
            
            # Clean up old videos when new upload happens
            print(f"New video uploaded: {original_filename}, cleaning up old videos...")
            cleanup_old_videos(max_files=app.config['MAX_STORED_VIDEOS'])

            output_filename = "processed_" + input_filename
            output_path = os.path.join(app.config['PROCESSED_FOLDER'], output_filename)

            # Get processing options from form
            options = {
                'transformation': request.form.get('transformation', 'none'),
                'filter': request.form.get('filter', 'none'),
                'speed': request.form.get('speed', '1.0'),
                'brightness': request.form.get('brightness', '1.0'),
                'contrast': request.form.get('contrast', '1.0')
            }

            # Create job
            create_job(job_id, original_filename, options)
            
            # Initialize cancellation system
            cancel_event = threading.Event()
            
            # Start background processing
            thread = threading.Thread(
                target=process_video_background,
                args=(job_id, input_path, output_path, options, original_filename)
            )
            thread.daemon = True
            
            # Register job in active jobs tracking
            active_jobs[job_id] = {
                'thread': thread,
                'should_cancel': cancel_event
            }
            
            thread.start()
            
            # Return job status page instead of direct redirect
            return redirect(url_for('job_status', job_id=job_id))
            
    return render_template('index.html')

@app.route('/job/<job_id>')
def job_status(job_id):
    """Show job status page with real-time progress"""
    job = get_job_status(job_id)
    if not job:
        return "Job not found", 404
    return render_template('job_status.html', job=job)

@app.route('/api/job/<job_id>/status')
def api_job_status(job_id):
    """API endpoint to get job status"""
    job = get_job_status(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(job)

@app.route('/api/job/<job_id>/cancel', methods=['POST'])
def api_cancel_job(job_id):
    """API endpoint to cancel a job"""
    success = cancel_job(job_id)
    if success:
        return jsonify({'message': 'Job cancellation requested', 'status': 'cancelled'})
    else:
        return jsonify({'error': 'Job not found or already completed'}), 404

@app.route('/play/<filename>')
def play_video(filename):
    expected_path = os.path.join(app.config['PROCESSED_FOLDER'], filename)
    if os.path.exists(expected_path):
        return render_template('video_player.html', filename=filename)
    return "Video not found", 404

@app.route('/processed/<filename>')
def processed_file(filename):
    file_path = os.path.join(app.config['PROCESSED_FOLDER'], filename)
    if not os.path.exists(file_path):
        from flask import abort
        abort(404, "Processed video not found.")
    return send_from_directory(app.config['PROCESSED_FOLDER'], filename)

# SocketIO Events
@socketio.on('connect')
def handle_connect():
    emit('connected', {'data': 'Connected to progress server'})

@socketio.on('join_job')
def handle_join_job(data):
    job_id = data['job_id']
    # Send current job status to the newly connected client
    job = get_job_status(job_id)
    if job:
        emit('progress_update', {
            'job_id': job_id,
            'progress': int(job.get('progress', 0)),
            'message': job.get('message', ''),
            'status': job.get('status', 'unknown')
        })

if __name__ == '__main__':
    # For production, use Gunicorn instead of running this directly
    # gunicorn --worker-class eventlet -w 1 --bind 0.0.0.0:5000 app:app
    import os
    port = int(os.environ.get('PORT', 5000))
    
    # Only allow development server in specific cases
    if os.environ.get('FLASK_ENV') == 'development':
        socketio.run(app, debug=True, host='0.0.0.0', port=port)
    else:
        # Production: This should not be reached - use Gunicorn instead
        print("⚠️  WARNING: Use Gunicorn for production deployment")
        print("   Command: gunicorn --worker-class eventlet -w 1 --bind 0.0.0.0:{} app:app".format(port))
        socketio.run(app, debug=False, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True) 