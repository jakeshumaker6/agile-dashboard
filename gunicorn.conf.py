# Gunicorn configuration for Render deployment

# Increase timeout to 120 seconds for slow ClickUp API calls
timeout = 120

# Number of workers
workers = 2

# Bind to PORT from environment
import os
bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"
