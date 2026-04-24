# SPDX-License-Identifier: Apache-2.0
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import sys
import os
import json
import queue
import threading

# Import the existing logic from our script
from email_finder import two_pass_find_email

app = Flask(__name__)
CORS(app) # Allow requests from the Chrome extension

@app.route('/find_email', methods=['POST'])
def api_find_email():
    data = request.json
    first_name = data.get('first_name')
    last_name = data.get('last_name')
    company = data.get('company')
    
    if not all([first_name, last_name, company]):
        return jsonify({"success": False, "message": "Missing required fields"}), 400
        
    print(f"\n[API] Received request for: {first_name} {last_name} @ {company}")

    def api_progress(msg, _current_email=None):
        print(msg)

    email = two_pass_find_email(
        first_name, last_name, company, progress_callback=api_progress
    )
    
    if email:
        print(f"[API] Success! Returning email: {email}")
        return jsonify({"success": True, "email": email})
    else:
        print(f"[API] Failed to find email.")
        return jsonify({"success": False, "message": "Could not find a valid email address."})

# Global dictionary to hold cancellation events for active sessions
active_sessions = {}

@app.route('/stop_search', methods=['POST'])
def stop_search():
    session_id = request.json.get('session_id')
    if session_id and session_id in active_sessions:
        active_sessions[session_id].set()
        return jsonify({"success": True, "message": "Search cancelled."})
    return jsonify({"success": False, "message": "Session not found."}), 404

@app.route('/stream_find_email')
def stream_find_email():
    first_name = request.args.get('first_name')
    last_name = request.args.get('last_name')
    company = request.args.get('company')
    session_id = request.args.get('session_id')
    
    if not all([first_name, last_name, company, session_id]):
        return jsonify({"success": False, "message": "Missing required fields"}), 400
        
    def generate():
        q = queue.Queue()
        cancel_event = threading.Event()
        active_sessions[session_id] = cancel_event
        
        def progress_callback(msg, current_email=None):
            q.put({"type": "progress", "message": msg, "current_email": current_email})
            
        def worker():
            try:
                if cancel_event.is_set():
                    return

                email = two_pass_find_email(
                    first_name,
                    last_name,
                    company,
                    progress_callback,
                    cancel_event,
                )
                
                if cancel_event.is_set():
                    q.put({"type": "result", "success": False, "message": "Search stopped by user.", "cancelled": True})
                elif email:
                    q.put({"type": "result", "success": True, "email": email})
                else:
                    q.put({"type": "result", "success": False, "message": "Could not find a valid email address. The server might be a catch-all or blocking requests."})
            except Exception as e:
                q.put({"type": "result", "success": False, "message": str(e)})
            finally:
                q.put({"type": "done"})
                if session_id in active_sessions:
                    del active_sessions[session_id]
                
        threading.Thread(target=worker).start()
        
        while True:
            item = q.get()
            yield f"data: {json.dumps(item)}\n\n"
            if item.get("type") == "done":
                break

    return Response(generate(), mimetype='text/event-stream')

if __name__ == '__main__':
    print("Starting Email Finder Backend Server on http://127.0.0.1:5000")
    print("Waiting for requests from the Chrome Extension...")
    app.run(port=5000, debug=True)