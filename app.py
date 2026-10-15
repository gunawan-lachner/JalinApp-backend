import os
from app import create_app

# Create the Flask app instance
app = create_app()

# Ensure instance directory exists
instance_path = os.path.join(os.path.dirname(__file__), 'instance')
os.makedirs(instance_path, exist_ok=True)

if __name__ == '__main__':
    import os
    port = int(os.getenv('PORT', 8005))
    host = os.getenv('HOST', '0.0.0.0')  # Use 0.0.0.0 for Docker networking
    debug = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(debug=debug, port=port, host=host)
