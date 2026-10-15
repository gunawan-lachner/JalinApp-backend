from app import app
import os

if __name__ == "__main__":
    port = int(os.getenv('PORT', 8005))
    host = os.getenv('HOST', '0.0.0.0')  # Use 0.0.0.0 for Docker networking
    debug = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(debug=debug, port=port, host=host)
