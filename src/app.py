from flask import Flask, request, jsonify, render_template, send_from_directory
from azure.storage.blob import BlobServiceClient
import os
from dotenv import load_dotenv
from datetime import datetime
from werkzeug.utils import secure_filename
import uuid

load_dotenv()

# Setup
CONN_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
CONTAINER_NAME = os.getenv("IMAGES_CONTAINER", "fooddonation")
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# Initialize Azure storage only if connection string is provided
USE_AZURE = CONN_STRING and CONN_STRING.strip()
bsc = None
cc = None

if USE_AZURE:
    try:
        bsc = BlobServiceClient.from_connection_string(CONN_STRING)
        cc = bsc.get_container_client(CONTAINER_NAME)
    except Exception as e:
        print(f"Warning: Failed to initialize Azure storage: {e}")
        USE_AZURE = False

# Setup local file storage as fallback
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '..', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__, template_folder=os.path.join(os.path.dirname(__file__), '..', 'templates'))
app.secret_key = "your-secret-key-change-in-production"
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10MB max
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# In-memory databases
donations_db = []  # Free donations from donors
bulk_boxes_db = []  # Discounted bulk boxes ($5)
orders_db = []  # Customer orders/carts
users_db = {}  # Simple user tracking

# Setup bulk items images directory
BULK_IMAGES_FOLDER = os.path.join(os.path.dirname(__file__), '..', 'uploads', 'bulk')
os.makedirs(BULK_IMAGES_FOLDER, exist_ok=True)

# Global bulk items - Images should be placed in uploads/bulk/ folder
bulk_items = [
    {
        "id": "bulk-1",
        "item_name": "Fresh Produce Bundle",
        "category": "produce",
        "quantity": "5 lbs mixed",
        "price": 5,
        "image_url": "/uploads/bulk/produce_bundle.jpg",
        "is_bulk": True
    },
    {
        "id": "bulk-2",
        "item_name": "Protein Pack",
        "category": "canned",
        "quantity": "8 cans protein",
        "price": 5,
        "image_url": "/uploads/bulk/protein_pack.jpg",
        "is_bulk": True
    },
    {
        "id": "bulk-3",
        "item_name": "Dairy Essentials",
        "category": "dairy",
        "quantity": "2L milk + eggs and bread",
        "price": 5,
        "image_url": "/uploads/bulk/dairy_bundle.jpg",
        "is_bulk": True
    }
]

# ============ HEALTH CHECK ============
@app.get("/api/health")
def health():
    return jsonify(ok=True, status="FoodBridge is running")

# ============ HOMEPAGE ============
@app.get("/")
def index():
    return render_template("index.html")

# ============ DONOR: UPLOAD FREE FOOD ============
@app.post("/api/donate")
def donate_food():
    """Donor uploads unopened food for FREE distribution"""
    try:
        item_name = request.form.get("item_name", "").strip()
        category = request.form.get("category", "other").strip()
        quantity = request.form.get("quantity", "").strip()
        expiration = request.form.get("expiration_date", "").strip()
        donor_name = request.form.get("donor_name", "Anonymous").strip()
        
        if not item_name or not quantity or not expiration:
            return jsonify(ok=False, error="Missing required fields"), 400
        
        if "file" not in request.files:
            return jsonify(ok=False, error="No image uploaded"), 400
        
        file = request.files["file"]
        if file.filename == "" or not allowed_file(file.filename):
            return jsonify(ok=False, error="Only image files allowed"), 400
        
        # Upload image (Azure or local)
        filename = secure_filename(file.filename)
        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        safe_filename = f"{timestamp}-{filename}"
        
        file.seek(0)  # Reset file pointer to beginning
        file_data = file.read()
        
        if USE_AZURE and bsc:
            try:
                # Upload to Azure Blob Storage
                blob_client = bsc.get_blob_client(CONTAINER_NAME, safe_filename)
                blob_client.upload_blob(data=file_data, overwrite=True)
                image_url = blob_client.url
            except Exception as e:
                print(f"Azure upload failed, falling back to local storage: {e}")
                # Fallback to local storage
                local_path = os.path.join(UPLOAD_FOLDER, safe_filename)
                with open(local_path, 'wb') as f:
                    f.write(file_data)
                image_url = f"/uploads/{safe_filename}"
        else:
            # Use local file storage
            local_path = os.path.join(UPLOAD_FOLDER, safe_filename)
            with open(local_path, 'wb') as f:
                f.write(file_data)
            image_url = f"/uploads/{safe_filename}"
        
        # Create donation entry
        donation = {
            "id": str(uuid.uuid4()),
            "item_name": item_name,
            "category": category,
            "quantity": quantity,
            "expiration_date": expiration,
            "donor_name": donor_name,
            "image_url": image_url,
            "posted_at": datetime.utcnow().isoformat(),
            "status": "available",  # available or claimed
            "price": 0  # FREE
        }
        donations_db.append(donation)
        
        return jsonify(ok=True, donation=donation), 201
    
    except Exception as e:
        print(f"Error donating: {e}")
        return jsonify(ok=False, error=str(e)), 500

# ============ RECIPIENT: BROWSE SHOP ============
@app.get("/api/shop")
def shop():
    """Recipients browse bulk boxes only (donations are separate)"""
    try:
        category = request.args.get("category", "").strip()
        
        # Only show bulk items in shop (donations are shown separately if needed)
        shop_items = bulk_items.copy()
        
        # Filter by category if specified
        if category and category != "all":
            shop_items = [item for item in shop_items if item.get("category") == category]
        
        return jsonify(ok=True, items=shop_items, count=len(shop_items)), 200
    
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

# ============ RECIPIENT: ADD TO CART ============
@app.post("/api/cart/add")
def add_to_cart():
    """Add item to cart"""
    try:
        user_id = request.form.get("user_id", str(uuid.uuid4()))
        item_id = request.form.get("item_id")
        quantity = int(request.form.get("quantity", 1))
        
        if not item_id:
            return jsonify(ok=False, error="Missing item_id"), 400
        
        # Initialize user cart if not exists
        if user_id not in users_db:
            users_db[user_id] = {"cart": []}
        
        # Add item to cart
        users_db[user_id]["cart"].append({
            "item_id": item_id,
            "quantity": quantity,
            "added_at": datetime.utcnow().isoformat()
        })
        
        return jsonify(ok=True, user_id=user_id, cart_count=len(users_db[user_id]["cart"])), 200
    
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

# ============ RECIPIENT: REMOVE FROM CART ============
@app.delete("/api/cart/remove")
@app.post("/api/cart/remove")  # Also support POST for easier form submission
def remove_from_cart():
    """Remove item from cart"""
    try:
        # Try form data first, then JSON
        user_id = request.form.get("user_id") or (request.json.get("user_id") if request.is_json else None)
        item_id = request.form.get("item_id") or (request.json.get("item_id") if request.is_json else None)
        
        if not user_id or not item_id:
            return jsonify(ok=False, error="Missing user_id or item_id"), 400
        
        if user_id not in users_db:
            return jsonify(ok=False, error="User not found"), 404
        
        # Remove first occurrence of item with matching item_id
        original_count = len(users_db[user_id]["cart"])
        users_db[user_id]["cart"] = [
            item for item in users_db[user_id]["cart"] 
            if item["item_id"] != item_id
        ]
        
        if len(users_db[user_id]["cart"]) == original_count:
            return jsonify(ok=False, error="Item not found in cart"), 404
        
        return jsonify(ok=True, message="Item removed from cart"), 200
    
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

# ============ RECIPIENT: GET CART ============
@app.get("/api/cart/<user_id>")
def get_cart(user_id):
    """Get user's cart"""
    try:
        if user_id not in users_db:
            return jsonify(ok=True, cart=[], total=0), 200
        
        cart_items = users_db[user_id]["cart"]
        
        # Populate item details and clean up invalid items
        detailed_cart = []
        total = 0
        valid_cart_items = []
        
        for cart_item in cart_items:
            # Find item in donations or bulk
            item = next((d for d in donations_db if d["id"] == cart_item["item_id"]), None)
            if not item:
                item = next((b for b in bulk_items if b["id"] == cart_item["item_id"]), None)
            
            if item:
                detailed_cart.append({
                    "item_id": cart_item["item_id"],
                    "item_name": item.get("item_name"),
                    "price": item.get("price", 0),
                    "quantity": cart_item["quantity"],
                    "subtotal": cart_item["quantity"] * item.get("price", 0)
                })
                total += cart_item["quantity"] * item.get("price", 0)
                valid_cart_items.append(cart_item)
        
        # Update cart to only include valid items (clean up orphaned items)
        users_db[user_id]["cart"] = valid_cart_items
        
        return jsonify(ok=True, cart=detailed_cart, total=total), 200
    
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

# ============ RECIPIENT: CHECKOUT ============
@app.post("/api/checkout")
def checkout():
    """Finalize order with pickup/delivery choice"""
    try:
        user_id = request.form.get("user_id")
        recipient_name = request.form.get("recipient_name", "").strip()
        phone = request.form.get("phone", "").strip()
        pickup_option = request.form.get("pickup_option")  # "pickup" or "delivery"
        preferred_date = request.form.get("preferred_date", "").strip()
        
        if not user_id or not recipient_name or not phone or not pickup_option:
            return jsonify(ok=False, error="Missing required fields"), 400
        
        if user_id not in users_db or not users_db[user_id]["cart"]:
            return jsonify(ok=False, error="Cart is empty"), 400
        
        # Create order
        order = {
            "order_id": str(uuid.uuid4()),
            "user_id": user_id,
            "recipient_name": recipient_name,
            "phone": phone,
            "items": users_db[user_id]["cart"],
            "pickup_option": pickup_option,
            "preferred_date": preferred_date,
            "status": "scheduled",  # scheduled, ready, completed
            "created_at": datetime.utcnow().isoformat()
        }
        orders_db.append(order)
        
        # Clear cart
        users_db[user_id]["cart"] = []
        
        return jsonify(ok=True, order=order, message="Order scheduled! Check back for updates."), 201
    
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

# ============ DONOR: VIEW DONATIONS ============
@app.get("/api/my-donations/<donor_name>")
def get_my_donations(donor_name):
    """Donor sees their donations"""
    try:
        my_items = [d for d in donations_db if d["donor_name"] == donor_name]
        return jsonify(ok=True, items=my_items, count=len(my_items)), 200
    
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

# ============ DONOR: REMOVE DONATION ============
@app.delete("/api/donation/<donation_id>")
def delete_donation(donation_id):
    """Donor removes their donation"""
    try:
        global donations_db
        original_count = len(donations_db)
        donations_db = [d for d in donations_db if d["id"] != donation_id]
        
        if len(donations_db) == original_count:
            return jsonify(ok=False, error="Donation not found"), 404
        
        return jsonify(ok=True, message="Donation removed"), 200
    
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

# ============ STATIC FILE SERVING ============
@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    """Serve uploaded images from local storage (supports nested paths like bulk/)"""
    # Handle nested paths like bulk/image.jpg
    if '/' in filename:
        folder, file = filename.rsplit('/', 1)
        folder_path = os.path.join(UPLOAD_FOLDER, folder)
        return send_from_directory(folder_path, file)
    else:
        return send_from_directory(UPLOAD_FOLDER, filename)

# ============ HELPER ============
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    app.run(debug=False, host="0.0.0.0", port=port)