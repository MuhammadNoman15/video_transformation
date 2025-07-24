# 🎬 Video Analysis Tool with Real-Time Progress

A comprehensive web-based video processing tool with real-time progress tracking and background processing capabilities.

## ✨ Features

### Core Video Processing
- **Video Transformations**: Grayscale, invert colors, flip (horizontal/vertical), rotate (90°/180°/270°)
- **OpenCV Filters**: Gaussian blur, sharpening with convolution kernels, Canny edge detection
- **Speed Control**: Adjust playback speed (0.25x to 2.0x)
- **Color Adjustments**: Brightness and contrast controls
- **Multiple Formats**: Support for MP4, AVI, MOV, MKV files

### Advanced Features
- **🔄 Background Processing**: Videos process in background threads
- **📊 Real-Time Progress**: Live progress updates via WebSocket
- **⚡ Job Management**: Track multiple processing jobs
- **📱 Responsive Design**: Professional UI for desktop and mobile
- **🎯 File Validation**: Smart file type and size validation
- **💾 Job Persistence**: Redis-based job storage with fallback

## 🚀 Quick Start

### Prerequisites
- Python 3.8+
- Redis Server (optional, falls back to in-memory storage)
- FFmpeg installed on system

### Installation

1. **Clone and Setup**
```bash
git clone <repository-url>
cd video-analysis-tool
pip install -r requirements.txt
```

2. **Start Redis (Optional but Recommended)**
```bash
# On Ubuntu/Debian
sudo apt-get install redis-server
sudo systemctl start redis-server

# On macOS with Homebrew
brew install redis
brew services start redis

# On Windows
# Download and install Redis from: https://redis.io/download
```

3. **Run the Application**
```bash
python app.py
```

4. **Access the Application**
- Open browser to: `http://localhost:5000`

## 🎯 How to Use

### Basic Processing
1. **Upload Video**: Drag & drop or click to select video file
2. **Choose Options**: Select transformations, filters, speed, etc.
3. **Process**: Click "Process Video" to start background processing
4. **Monitor Progress**: Watch real-time progress updates
5. **Download**: Get your processed video when complete

### Advanced Features
- **Multiple Jobs**: Process multiple videos simultaneously
- **Progress Tracking**: See detailed progress for each processing stage
- **Connection Status**: Monitor WebSocket connection status
- **Error Handling**: Clear error messages and recovery options

## 🛠️ Technical Architecture

### Backend Components
- **Flask**: Web framework with SocketIO for real-time communication
- **OpenCV**: Computer vision processing for filters and effects
- **FFmpeg**: Video encoding, transformations, and format conversion
- **Redis**: Job storage and management (with in-memory fallback)
- **Threading**: Background processing without blocking main thread

### Processing Pipeline
1. **Upload & Validation**: File type, size, and format validation
2. **Job Creation**: Generate unique job ID and initialize tracking
3. **Background Processing**: 
   - FFmpeg transformations (5-30%)
   - OpenCV filtering with frame-by-frame progress (30-75%)
   - Final encoding and speed adjustment (75-95%)
   - Verification and cleanup (95-100%)
4. **Real-Time Updates**: WebSocket progress emissions
5. **Completion**: Redirect to video player or error handling

### Frontend Features
- **WebSocket Integration**: Real-time progress updates
- **Responsive Design**: Professional UI across all devices
- **Progress Visualization**: Animated progress bars and status badges
- **Error Recovery**: User-friendly error messages and retry options

## 📊 API Endpoints

### Web Routes
- `GET /` - Upload form
- `POST /` - Start video processing
- `GET /job/<job_id>` - Job status page
- `GET /play/<filename>` - Video player
- `GET /processed/<filename>` - Download processed video

### API Routes
- `GET /api/job/<job_id>/status` - Job status JSON

### WebSocket Events
- `connect` - Client connection established
- `join_job` - Subscribe to job updates
- `progress_update` - Real-time progress data
- `job_completed` - Processing completion notification

## 🔧 Configuration

### Environment Variables
```bash
# Optional Redis configuration
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0

# Flask configuration
FLASK_ENV=development
SECRET_KEY=your-secret-key-here
```

### File Limits
- Maximum file size: 100MB
- Supported formats: MP4, AVI, MOV, MKV
- Processing timeout: 30 minutes per job

## 🚦 System Requirements

### Minimum Requirements
- 2GB RAM
- 1GB free disk space
- Python 3.8+
- FFmpeg installed

### Recommended Requirements
- 4GB+ RAM for large video processing
- SSD storage for better I/O performance
- Redis server for production deployment
- Multi-core CPU for faster processing

## 🧪 Testing

### Test Processing
1. Upload a small test video (< 10MB)
2. Apply various transformations and filters
3. Monitor progress updates in real-time
4. Verify output quality and format

### Browser Compatibility
- Chrome 90+ (recommended)
- Firefox 88+
- Safari 14+
- Edge 90+

## 🔍 Troubleshooting

### Common Issues
1. **Redis Connection Failed**: App falls back to in-memory storage
2. **FFmpeg Not Found**: Install FFmpeg and add to system PATH
3. **Large File Processing**: Increase system memory or reduce file size
4. **WebSocket Connection Issues**: Check firewall and proxy settings

### Debug Mode
Run with debug logging:
```bash
FLASK_ENV=development python app.py
```

## 📈 Performance Optimization

### For Production
- Use Redis for job storage
- Configure proper process limits
- Use WSGI server (Gunicorn/uWSGI)
- Implement file cleanup routines
- Add monitoring and logging

### Scaling Considerations
- Multiple worker processes
- Load balancing for WebSocket connections
- Distributed job queues (Celery)
- File storage optimization

## 🤝 Contributing

1. Fork the repository
2. Create feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open Pull Request

## 📝 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🙏 Acknowledgments

- OpenCV community for computer vision tools
- FFmpeg project for video processing capabilities
- Flask and SocketIO for web framework and real-time communication
- Redis for efficient job management 