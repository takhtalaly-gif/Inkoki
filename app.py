"""
🚀 INKO Backend with Supabase Storage Support
"""

import os
import json
import hashlib
import uuid
import time
import base64
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from flask_cors import CORS

# ✅ FIX: psycopg2-binary for Python 3.14 compatibility
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    import psycopg as psycopg2
    from psycopg.rows import dict_row as RealDictCursor

from dotenv import load_dotenv
from supabase import create_client, Client

# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app)

# Database connection
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    raise Exception("DATABASE_URL environment variable not set!")

# Supabase Storage
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

if not SUPABASE_URL or not SUPABASE_KEY:
    print("WARNING: Supabase storage not configured. File uploads will fail.")
    supabase = None
else:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def get_db():
    """Get database connection"""
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn

def hash_password(password):
    """Hash password using SHA256"""
    return hashlib.sha256(password.encode()).hexdigest()

def format_timestamp(dt):
    """Convert datetime to unix timestamp"""
    if isinstance(dt, datetime):
        return int(dt.timestamp())
    return dt

# ==================== FILE UPLOAD HELPERS ====================

def upload_to_supabase(file_data, file_name, bucket='posts'):
    """
    Upload file to Supabase Storage
    
    Args:
        file_data: Base64 encoded file data or raw bytes
        file_name: Name of the file
        bucket: Storage bucket name ('posts', 'stories', 'avatars')
    
    Returns:
        Public URL of uploaded file or None if failed
    """
    if not supabase:
        return None
    
    try:
        # If base64, decode it
        if isinstance(file_data, str):
            if ',' in file_data:
                # Remove data:image/jpeg;base64, prefix
                file_data = file_data.split(',')[1]
            file_data = base64.b64decode(file_data)
        
        # Generate unique filename
        ext = file_name.split('.')[-1] if '.' in file_name else 'jpg'
        unique_name = f"{uuid.uuid4()}.{ext}"
        
        # Upload to Supabase Storage
        result = supabase.storage.from_(bucket).upload(
            path=unique_name,
            file=file_data,
            file_options={"content-type": f"image/{ext}"}
        )
        
        # Get public URL
        public_url = supabase.storage.from_(bucket).get_public_url(unique_name)
        
        return public_url
    
    except Exception as e:
        print(f"Upload error: {e}")
        return None

# ==================== AUTH ROUTES ====================

@app.route('/api/auth/signup', methods=['POST'])
def signup():
    """Register new user"""
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')
    
    if not username or not password:
        return jsonify({'error': 'Missing required fields'}), 400
    
    if len(username) < 3 or len(username) > 30:
        return jsonify({'error': 'Username must be 3-30 characters'}), 400
    
    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(%s)", (username,))
        if cur.fetchone():
            return jsonify({'error': 'Username already exists'}), 400
        
        hashed_pw = hash_password(password)
        cur.execute(
            "INSERT INTO users (username, password) VALUES (%s, %s) RETURNING id, username, bio, profile_pic, created_at",
            (username, hashed_pw)
        )
        user = dict(cur.fetchone())
        user['created_at'] = format_timestamp(user['created_at'])
        conn.commit()
        
        return jsonify({'success': True, 'user': user})
    
    except Exception as e:
        conn.rollback()
        print(f"Signup error: {e}")
        return jsonify({'error': 'Signup failed'}), 500
    
    finally:
        cur.close()
        conn.close()

@app.route('/api/auth/login', methods=['POST'])
def login():
    """Login user"""
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')
    
    if not username or not password:
        return jsonify({'error': 'Missing required fields'}), 400
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute(
            "SELECT id, username, password, bio, profile_pic, created_at FROM users WHERE LOWER(username) = LOWER(%s)",
            (username,)
        )
        user = cur.fetchone()
        
        if not user or dict(user)['password'] != hash_password(password):
            return jsonify({'error': 'Invalid credentials'}), 401
        
        user_dict = dict(user)
        del user_dict['password']
        user_dict['created_at'] = format_timestamp(user_dict['created_at'])
        
        return jsonify({'success': True, 'user': user_dict})
    
    except Exception as e:
        print(f"Login error: {e}")
        return jsonify({'error': 'Login failed'}), 500
    
    finally:
        cur.close()
        conn.close()

# ==================== UPLOAD ROUTES ====================

@app.route('/api/post/upload', methods=['POST'])
def upload_post():
    """Upload new post with image/video"""
    data = request.json
    user_id = data.get('user_id')
    caption = data.get('caption', '')
    file_data = data.get('file')  # Base64 encoded
    file_name = data.get('file_name', 'image.jpg')
    media_type = data.get('media_type', 'image')  # 'image' or 'video'
    
    if not user_id or not file_data:
        return jsonify({'error': 'Missing required fields'}), 400
    
    # Upload to Supabase Storage
    public_url = upload_to_supabase(file_data, file_name, 'posts')
    
    if not public_url:
        return jsonify({'error': 'Failed to upload file'}), 500
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        # Create post
        cur.execute(
            """INSERT INTO posts (user_id, caption, media_type, media_url) 
               VALUES (%s, %s, %s, %s) 
               RETURNING id, user_id, caption, media_type, media_url, created_at""",
            (user_id, caption[:2200], media_type, public_url)
        )
        post = dict(cur.fetchone())
        post['created_at'] = format_timestamp(post['created_at'])
        
        # Create notification for followers
        cur.execute("""
            INSERT INTO notifications (user_id, from_user_id, type, post_id)
            SELECT follower_id, %s, 'post', %s
            FROM follows WHERE following_id = %s
        """, (user_id, post['id'], user_id))
        
        conn.commit()
        
        return jsonify({'success': True, 'post': post})
    
    except Exception as e:
        conn.rollback()
        print(f"Post upload error: {e}")
        return jsonify({'error': 'Failed to create post'}), 500
    
    finally:
        cur.close()
        conn.close()

@app.route('/api/story/upload', methods=['POST'])
def upload_story():
    """Upload new story"""
    data = request.json
    user_id = data.get('user_id')
    file_data = data.get('file')  # Base64 encoded
    file_name = data.get('file_name', 'story.jpg')
    media_type = data.get('media_type', 'image')
    
    if not user_id or not file_data:
        return jsonify({'error': 'Missing required fields'}), 400
    
    # Upload to Supabase Storage
    public_url = upload_to_supabase(file_data, file_name, 'stories')
    
    if not public_url:
        return jsonify({'error': 'Failed to upload file'}), 500
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        # Create story
        cur.execute(
            """INSERT INTO stories (user_id, media_type, media_url) 
               VALUES (%s, %s, %s) 
               RETURNING id, user_id, media_type, media_url, created_at""",
            (user_id, media_type, public_url)
        )
        story = dict(cur.fetchone())
        story['created_at'] = format_timestamp(story['created_at'])
        
        # Create notification for followers
        cur.execute("""
            INSERT INTO notifications (user_id, from_user_id, type)
            SELECT follower_id, %s, 'story'
            FROM follows WHERE following_id = %s
        """, (user_id, user_id))
        
        conn.commit()
        
        return jsonify({'success': True, 'story': story})
    
    except Exception as e:
        conn.rollback()
        print(f"Story upload error: {e}")
        return jsonify({'error': 'Failed to create story'}), 500
    
    finally:
        cur.close()
        conn.close()

@app.route('/api/profile/upload-avatar', methods=['POST'])
def upload_avatar():
    """Upload profile avatar"""
    data = request.json
    user_id = data.get('user_id')
    file_data = data.get('file')  # Base64 encoded
    file_name = data.get('file_name', 'avatar.jpg')
    
    if not user_id or not file_data:
        return jsonify({'error': 'Missing required fields'}), 400
    
    # Upload to Supabase Storage
    public_url = upload_to_supabase(file_data, file_name, 'avatars')
    
    if not public_url:
        return jsonify({'error': 'Failed to upload file'}), 500
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        # Update user profile
        cur.execute(
            "UPDATE users SET profile_pic = %s WHERE id = %s RETURNING id, username, bio, profile_pic",
            (public_url, user_id)
        )
        user = dict(cur.fetchone())
        conn.commit()
        
        return jsonify({'success': True, 'user': user})
    
    except Exception as e:
        conn.rollback()
        print(f"Avatar upload error: {e}")
        return jsonify({'error': 'Failed to upload avatar'}), 500
    
    finally:
        cur.close()
        conn.close()

# ==================== FEED ROUTES ====================

@app.route('/api/feed', methods=['GET'])
def get_feed():
    """Get user feed"""
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute("""
            SELECT DISTINCT p.*, u.username, u.profile_pic as user_profile_pic,
                   (SELECT COUNT(*) FROM likes WHERE post_id = p.id) as likes_count,
                   (SELECT COUNT(*) FROM comments WHERE post_id = p.id) as comments_count,
                   (SELECT json_agg(user_id) FROM likes WHERE post_id = p.id) as likes
            FROM posts p
            JOIN users u ON p.user_id = u.id
            WHERE p.user_id IN (
                SELECT following_id FROM follows WHERE follower_id = %s
                UNION SELECT %s
            )
            ORDER BY p.created_at DESC
            LIMIT 50
        """, (user_id, user_id))
        
        posts = []
        for row in cur.fetchall():
            post = dict(row)
            post['created_at'] = format_timestamp(post['created_at'])
            post['likes'] = post['likes'] or []
            posts.append(post)
        
        return jsonify({'posts': posts})
    
    except Exception as e:
        print(f"Feed error: {e}")
        return jsonify({'error': 'Failed to load feed'}), 500
    
    finally:
        cur.close()
        conn.close()

# ==================== EXPLORE ====================

@app.route('/api/explore', methods=['GET'])
def get_explore():
    """Get explore posts"""
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute("""
            SELECT p.*, u.username, u.profile_pic as user_profile_pic,
                   (SELECT COUNT(*) FROM likes WHERE post_id = p.id) as likes_count,
                   (SELECT COUNT(*) FROM comments WHERE post_id = p.id) as comments_count
            FROM posts p
            JOIN users u ON p.user_id = u.id
            ORDER BY p.created_at DESC
            LIMIT 30
        """)
        
        posts = []
        for row in cur.fetchall():
            post = dict(row)
            post['created_at'] = format_timestamp(post['created_at'])
            posts.append(post)
        
        return jsonify({'posts': posts})
    
    except Exception as e:
        print(f"Explore error: {e}")
        return jsonify({'error': 'Failed to load explore'}), 500
    
    finally:
        cur.close()
        conn.close()

# ==================== STORIES ====================

@app.route('/api/stories', methods=['GET'])
def get_stories():
    """Get feed stories"""
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        # Delete expired stories first
        cur.execute("DELETE FROM stories WHERE expires_at < NOW()")
        conn.commit()
        
        # Get stories from followed users
        cur.execute("""
            SELECT s.*, u.username, u.profile_pic as user_profile_pic,
                   (SELECT json_agg(user_id) FROM story_views WHERE story_id = s.id) as views
            FROM stories s
            JOIN users u ON s.user_id = u.id
            WHERE s.user_id IN (
                SELECT following_id FROM follows WHERE follower_id = %s
            )
            AND s.expires_at > NOW()
            ORDER BY s.created_at DESC
        """, (user_id,))
        
        # Group by user
        stories_dict = {}
        for row in cur.fetchall():
            story = dict(row)
            story['created_at'] = format_timestamp(story['created_at'])
            story['views'] = story['views'] or []
            
            user_id_key = story['user_id']
            if user_id_key not in stories_dict:
                stories_dict[user_id_key] = {
                    'user_id': user_id_key,
                    'username': story['username'],
                    'user_profile_pic': story['user_profile_pic'],
                    'stories': []
                }
            
            stories_dict[user_id_key]['stories'].append({
                'id': story['id'],
                'media_type': story['media_type'],
                'media_url': story['media_url'],
                'created_at': story['created_at'],
                'views': story['views']
            })
        
        return jsonify({'stories': list(stories_dict.values())})
    
    except Exception as e:
        print(f"Stories error: {e}")
        return jsonify({'error': 'Failed to load stories'}), 500
    
    finally:
        cur.close()
        conn.close()

@app.route('/api/story/view', methods=['POST'])
def add_story_view():
    """Add story view"""
    data = request.json
    user_id = data.get('user_id')
    story_id = data.get('story_id')
    
    if not user_id or not story_id:
        return jsonify({'error': 'Missing required fields'}), 400
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        # Add view (ON CONFLICT DO NOTHING to avoid duplicates)
        cur.execute(
            "INSERT INTO story_views (story_id, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (story_id, user_id)
        )
        conn.commit()
        
        return jsonify({'success': True})
    
    except Exception as e:
        conn.rollback()
        print(f"Story view error: {e}")
        return jsonify({'error': 'Failed to add view'}), 500
    
    finally:
        cur.close()
        conn.close()

# ==================== LIKES & COMMENTS ====================

@app.route('/api/post/like', methods=['POST'])
def like_post():
    """Like/unlike post"""
    data = request.json
    user_id = data.get('user_id')
    post_id = data.get('post_id')
    
    if not user_id or not post_id:
        return jsonify({'error': 'Missing required fields'}), 400
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute("SELECT id FROM likes WHERE user_id = %s AND post_id = %s", (user_id, post_id))
        existing = cur.fetchone()
        
        if existing:
            cur.execute("DELETE FROM likes WHERE user_id = %s AND post_id = %s", (user_id, post_id))
            liked = False
        else:
            cur.execute("INSERT INTO likes (user_id, post_id) VALUES (%s, %s)", (user_id, post_id))
            
            # Notification
            cur.execute("SELECT user_id FROM posts WHERE id = %s", (post_id,))
            post_owner = cur.fetchone()
            if post_owner and dict(post_owner)['user_id'] != user_id:
                cur.execute(
                    "INSERT INTO notifications (user_id, from_user_id, type, post_id) VALUES (%s, %s, 'like', %s)",
                    (dict(post_owner)['user_id'], user_id, post_id)
                )
            liked = True
        
        conn.commit()
        return jsonify({'success': True, 'liked': liked})
    
    except Exception as e:
        conn.rollback()
        print(f"Like error: {e}")
        return jsonify({'error': 'Failed'}), 500
    
    finally:
        cur.close()
        conn.close()

@app.route('/api/post/comment', methods=['POST'])
def add_comment():
    """Add comment"""
    data = request.json
    user_id = data.get('user_id')
    post_id = data.get('post_id')
    text = data.get('text', '').strip()
    
    if not user_id or not post_id or not text:
        return jsonify({'error': 'Missing required fields'}), 400
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute(
            "INSERT INTO comments (user_id, post_id, text) VALUES (%s, %s, %s) RETURNING id, created_at",
            (user_id, post_id, text[:500])
        )
        comment = dict(cur.fetchone())
        comment['created_at'] = format_timestamp(comment['created_at'])
        
        # Notification
        cur.execute("SELECT user_id FROM posts WHERE id = %s", (post_id,))
        post_owner = cur.fetchone()
        if post_owner and dict(post_owner)['user_id'] != user_id:
            cur.execute(
                "INSERT INTO notifications (user_id, from_user_id, type, post_id) VALUES (%s, %s, 'comment', %s)",
                (dict(post_owner)['user_id'], user_id, post_id)
            )
        
        conn.commit()
        return jsonify({'success': True, 'comment': comment})
    
    except Exception as e:
        conn.rollback()
        print(f"Comment error: {e}")
        return jsonify({'error': 'Failed'}), 500
    
    finally:
        cur.close()
        conn.close()

@app.route('/api/post/comments', methods=['GET'])
def get_comments():
    """Get comments"""
    post_id = request.args.get('post_id')
    if not post_id:
        return jsonify({'error': 'Missing post_id'}), 400
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute("""
            SELECT c.*, u.username, u.profile_pic as user_profile_pic
            FROM comments c
            JOIN users u ON c.user_id = u.id
            WHERE c.post_id = %s
            ORDER BY c.created_at ASC
        """, (post_id,))
        
        comments = []
        for row in cur.fetchall():
            comment = dict(row)
            comment['created_at'] = format_timestamp(comment['created_at'])
            comments.append(comment)
        
        return jsonify({'comments': comments})
    
    except Exception as e:
        print(f"Get comments error: {e}")
        return jsonify({'error': 'Failed'}), 500
    
    finally:
        cur.close()
        conn.close()

# ==================== FOLLOW ====================

@app.route('/api/follow', methods=['POST'])
def toggle_follow():
    """Follow/unfollow"""
    data = request.json
    follower_id = data.get('follower_id')
    following_id = data.get('following_id')
    
    if not follower_id or not following_id or follower_id == following_id:
        return jsonify({'error': 'Invalid request'}), 400
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute("SELECT id FROM follows WHERE follower_id = %s AND following_id = %s", (follower_id, following_id))
        existing = cur.fetchone()
        
        if existing:
            cur.execute("DELETE FROM follows WHERE follower_id = %s AND following_id = %s", (follower_id, following_id))
            followed = False
        else:
            cur.execute("INSERT INTO follows (follower_id, following_id) VALUES (%s, %s)", (follower_id, following_id))
            cur.execute(
                "INSERT INTO notifications (user_id, from_user_id, type) VALUES (%s, %s, 'follow')",
                (following_id, follower_id)
            )
            followed = True
        
        conn.commit()
        return jsonify({'success': True, 'followed': followed})
    
    except Exception as e:
        conn.rollback()
        print(f"Follow error: {e}")
        return jsonify({'error': 'Failed'}), 500
    
    finally:
        cur.close()
        conn.close()

# ==================== SEARCH & PROFILE ====================

@app.route('/api/users/search', methods=['GET'])
def search_users():
    """Search users"""
    query = request.args.get('query', '').strip()
    user_id = request.args.get('user_id')
    
    if not query:
        return jsonify({'users': []})
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute("""
            SELECT u.id, u.username, u.bio, u.profile_pic,
                   EXISTS(SELECT 1 FROM follows WHERE follower_id = %s AND following_id = u.id) as is_following
            FROM users u
            WHERE u.username ILIKE %s AND u.id != %s
            LIMIT 20
        """, (user_id, f'%{query}%', user_id))
        
        users = [dict(row) for row in cur.fetchall()]
        return jsonify({'users': users})
    
    except Exception as e:
        print(f"Search error: {e}")
        return jsonify({'error': 'Search failed'}), 500
    
    finally:
        cur.close()
        conn.close()

@app.route('/api/profile', methods=['GET'])
def get_profile():
    """Get profile"""
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute("SELECT id, username, bio, profile_pic, created_at FROM users WHERE id = %s", (user_id,))
        user = cur.fetchone()
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        profile = dict(user)
        profile['created_at'] = format_timestamp(profile['created_at'])
        
        cur.execute("""
            SELECT p.*, 
                   (SELECT COUNT(*) FROM likes WHERE post_id = p.id) as likes_count,
                   (SELECT COUNT(*) FROM comments WHERE post_id = p.id) as comments_count
            FROM posts p
            WHERE p.user_id = %s
            ORDER BY p.created_at DESC
        """, (user_id,))
        
        posts = []
        for row in cur.fetchall():
            post = dict(row)
            post['created_at'] = format_timestamp(post['created_at'])
            posts.append(post)
        
        cur.execute("SELECT COUNT(*) FROM follows WHERE following_id = %s", (user_id,))
        followers_count = cur.fetchone()['count']
        
        cur.execute("SELECT COUNT(*) FROM follows WHERE follower_id = %s", (user_id,))
        following_count = cur.fetchone()['count']
        
        return jsonify({
            'profile': profile,
            'posts': posts,
            'posts_count': len(posts),
            'followers_count': followers_count,
            'following_count': following_count
        })
    
    except Exception as e:
        print(f"Profile error: {e}")
        return jsonify({'error': 'Failed'}), 500
    
    finally:
        cur.close()
        conn.close()

@app.route('/api/profile/update', methods=['POST'])
def update_profile():
    """Update profile"""
    data = request.json
    user_id = data.get('user_id')
    bio = data.get('bio')
    
    if not user_id:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute(
            "UPDATE users SET bio = %s WHERE id = %s RETURNING id, username, bio, profile_pic",
            (bio[:200] if bio else '', user_id)
        )
        user = dict(cur.fetchone())
        conn.commit()
        
        return jsonify({'success': True, 'user': user})
    
    except Exception as e:
        conn.rollback()
        print(f"Update error: {e}")
        return jsonify({'error': 'Failed'}), 500
    
    finally:
        cur.close()
        conn.close()

# ==================== NOTIFICATIONS ====================

@app.route('/api/notifications', methods=['GET'])
def get_notifications():
    """Get notifications"""
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute("""
            SELECT n.*, u.username as from_username, u.profile_pic as from_profile_pic,
                   p.media_url as post_media
            FROM notifications n
            JOIN users u ON n.from_user_id = u.id
            LEFT JOIN posts p ON n.post_id = p.id
            WHERE n.user_id = %s
            ORDER BY n.created_at DESC
            LIMIT 50
        """, (user_id,))
        
        notifications = []
        for row in cur.fetchall():
            notif = dict(row)
            notif['created_at'] = format_timestamp(notif['created_at'])
            notifications.append(notif)
        
        cur.execute("SELECT COUNT(*) FROM notifications WHERE user_id = %s AND read = FALSE", (user_id,))
        unread_count = cur.fetchone()['count']
        
        return jsonify({
            'notifications': notifications,
            'unread_count': unread_count
        })
    
    except Exception as e:
        print(f"Notifications error: {e}")
        return jsonify({'error': 'Failed'}), 500
    
    finally:
        cur.close()
        conn.close()

@app.route('/api/notification/read', methods=['POST'])
def mark_notification_read():
    """Mark as read"""
    data = request.json
    user_id = data.get('user_id')
    notification_id = data.get('notification_id')
    
    if not user_id:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        if notification_id:
            cur.execute("UPDATE notifications SET read = TRUE WHERE id = %s AND user_id = %s", (notification_id, user_id))
        else:
            cur.execute("UPDATE notifications SET read = TRUE WHERE user_id = %s", (user_id,))
        
        conn.commit()
        return jsonify({'success': True})
    
    except Exception as e:
        conn.rollback()
        print(f"Mark read error: {e}")
        return jsonify({'error': 'Failed'}), 500
    
    finally:
        cur.close()
        conn.close()

# ==================== HEALTH ====================

@app.route('/health', methods=['GET'])
def health_check():
    """Health check"""
    return jsonify({
        'status': 'ok',
        'storage': 'supabase' if supabase else 'disabled'
    })

@app.route('/', methods=['GET'])
def home():
    """Home"""
    return jsonify({
        'name': 'INKO API with Storage',
        'version': '1.0',
        'storage': 'Supabase Storage',
        'status': 'running'
    })

# ==================== RUN ====================

if __name__ == '__main__':
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)
