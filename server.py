import json
import sqlite3
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

DB_FILE = 'gantt.db'
PORT = 8000

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS app_state (id INTEGER PRIMARY KEY, json_data TEXT)')
    
    # Check if empty
    c.execute('SELECT COUNT(*) FROM app_state')
    count = c.fetchone()[0]
    if count == 0:
        c.execute("INSERT INTO app_state (id, json_data) VALUES (1, '{}')")
        conn.commit()
    conn.close()

class RequestHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        if self.path == '/api/data':
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute('SELECT json_data FROM app_state WHERE id = 1')
            row = c.fetchone()
            conn.close()
            
            data = row[0] if row else '{}'
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.end_headers()
            self.wfile.write(data.encode('utf-8'))
        elif self.path == '/' or self.path == '/gantt-editor.html':
            try:
                # Use absolute path resolving relative to server.py location
                base_dir = os.path.dirname(os.path.abspath(__file__))
                file_path = os.path.join(base_dir, 'gantt-editor.html')
                with open(file_path, 'rb') as f:
                    content = f.read()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b'gantt-editor.html not found')
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == '/api/data':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            try:
                # Validate JSON format
                json_data = json.loads(post_data.decode('utf-8'))
                
                conn = sqlite3.connect(DB_FILE)
                c = conn.cursor()
                # Update the state
                c.execute('UPDATE app_state SET json_data = ? WHERE id = 1', (json.dumps(json_data),))
                conn.commit()
                conn.close()
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(b'{"status": "success"}')
            except json.JSONDecodeError:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error": "Invalid JSON"}')
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(f'{{"error": "{str(e)}"}}'.encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

def run():
    init_db()
    server_address = ('', PORT)
    httpd = HTTPServer(server_address, RequestHandler)
    print(f'Starte Server auf http://localhost:{PORT}')
    print(f'Öffnen Sie http://localhost:{PORT} in Ihrem Browser.')
    print('Drücken Sie STRG+C zum Beenden.')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServer wird beendet.")
        httpd.server_close()

if __name__ == '__main__':
    run()
