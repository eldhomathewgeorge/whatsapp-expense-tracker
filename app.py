from flask import Flask, request
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
import os
from datetime import datetime
import re
import json

app = Flask(__name__)

# Configure Google AI (Gemini)
genai.configure(api_key=os.environ.get("GOOGLE_AI_API_KEY"))
model = genai.GenerativeModel('gemini-pro')

# Google Sheets setup
def get_sheets_client():
    """Initialize Google Sheets client"""
    try:
        # Get credentials from environment variable (JSON string)
        creds_json = os.environ.get('GOOGLE_SHEETS_CREDENTIALS')
        if creds_json:
            creds_dict = json.loads(creds_json)
            creds = Credentials.from_service_account_info(
                creds_dict,
                scopes=['https://www.googleapis.com/auth/spreadsheets']
            )
        else:
            # For local development, use credentials file
            creds = Credentials.from_service_account_file(
                'credentials.json',
                scopes=['https://www.googleapis.com/auth/spreadsheets']
            )
        
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        print(f"Error initializing sheets client: {e}")
        return None

def get_or_create_sheet():
    """Get or create the expense tracking spreadsheet"""
    client = get_sheets_client()
    if not client:
        return None
    
    sheet_name = "ExpenseTracker"
    
    try:
        # Try to open existing spreadsheet
        spreadsheet = client.open(sheet_name)
        worksheet = spreadsheet.sheet1
    except gspread.SpreadsheetNotFound:
        # Create new spreadsheet
        spreadsheet = client.create(sheet_name)
        worksheet = spreadsheet.sheet1
        
        # Set up headers
        worksheet.update('A1:E1', [['Date', 'Description', 'Amount', 'Category', 'Timestamp']])
        
        # Share with your email (optional - update with your email)
        # spreadsheet.share('your-email@gmail.com', perm_type='user', role='writer')
    
    return worksheet

# AI categorization using Google Gemini
def categorize_expense(description):
    """Use Gemini to categorize the expense"""
    try:
        prompt = f"""Categorize this expense into ONE word category: "{description}"

Common categories: Food, Transport, Shopping, Entertainment, Bills, Health, Education, Groceries, Other

Respond with ONLY the category name, nothing else."""
        
        response = model.generate_content(prompt)
        category = response.text.strip()
        return category
    except Exception as e:
        print(f"Error categorizing: {e}")
        return "Other"

# Parse expense from message
def parse_expense(text):
    """Extract description and amount from text like 'Lunch 15.50'"""
    # Try pattern: "description amount"
    match = re.search(r'(.+?)\s+([\d.]+)\s*$', text.strip())
    if match:
        description = match.group(1).strip()
        amount = float(match.group(2))
        return description, amount
    
    # Try pattern: "amount description"
    match = re.search(r'^([\d.]+)\s+(.+)', text.strip())
    if match:
        amount = float(match.group(1))
        description = match.group(2).strip()
        return description, amount
    
    return None, None

# Save expense to Google Sheets
def save_expense(description, amount, category):
    """Save expense to Google Sheets"""
    try:
        worksheet = get_or_create_sheet()
        if not worksheet:
            return False
        
        date = datetime.now().strftime('%Y-%m-%d')
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Append new row
        worksheet.append_row([date, description, amount, category, timestamp])
        return True
    except Exception as e:
        print(f"Error saving expense: {e}")
        return False

# Get summary from Google Sheets
def get_summary(period='today'):
    """Get expense summary from Google Sheets"""
    try:
        worksheet = get_or_create_sheet()
        if not worksheet:
            return [], 0
        
        # Get all records
        records = worksheet.get_all_records()
        
        # Filter by period
        today = datetime.now().date()
        filtered = []
        
        for record in records:
            try:
                record_date = datetime.strptime(record['Date'], '%Y-%m-%d').date()
                
                if period == 'today' and record_date == today:
                    filtered.append(record)
                elif period == 'week' and (today - record_date).days <= 7:
                    filtered.append(record)
                elif period == 'month' and (today - record_date).days <= 30:
                    filtered.append(record)
            except:
                continue
        
        # Calculate totals by category
        category_totals = {}
        total = 0
        
        for record in filtered:
            try:
                amount = float(record['Amount'])
                category = record['Category']
                
                if category in category_totals:
                    category_totals[category] += amount
                else:
                    category_totals[category] = amount
                
                total += amount
            except:
                continue
        
        results = [(cat, amt) for cat, amt in category_totals.items()]
        return results, total
        
    except Exception as e:
        print(f"Error getting summary: {e}")
        return [], 0

# Twilio webhook endpoint
@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle incoming WhatsApp messages"""
    incoming_msg = request.values.get('Body', '').strip().lower()
    from_number = request.values.get('From', '')
    
    response_msg = ""
    
    # Check for commands
    if incoming_msg in ['summary', 'today']:
        results, total = get_summary('today')
        response_msg = f"📊 *Today's Expenses: ${total:.2f}*\n\n"
        if results:
            for cat, amt in results:
                response_msg += f"• {cat}: ${amt:.2f}\n"
        else:
            response_msg += "No expenses recorded today."
    
    elif incoming_msg == 'week':
        results, total = get_summary('week')
        response_msg = f"📊 *This Week: ${total:.2f}*\n\n"
        if results:
            for cat, amt in results:
                response_msg += f"• {cat}: ${amt:.2f}\n"
        else:
            response_msg += "No expenses this week."
    
    elif incoming_msg == 'month':
        results, total = get_summary('month')
        response_msg = f"📊 *This Month: ${total:.2f}*\n\n"
        if results:
            for cat, amt in results:
                response_msg += f"• {cat}: ${amt:.2f}\n"
        else:
            response_msg += "No expenses this month."
    
    elif incoming_msg == 'help':
        response_msg = """🤖 *Expense Tracker Help*

📝 Add expense:
• "Lunch 15.50"
• "Coffee 5.25"
• "Uber 12"

📊 View summaries:
• "summary" or "today"
• "week"
• "month"

💾 Data stored in Google Sheets
🤖 AI categorizes automatically!"""
    
    else:
        # Try to parse as expense
        original_msg = request.values.get('Body', '').strip()
        description, amount = parse_expense(original_msg)
        
        if description and amount:
            try:
                category = categorize_expense(description)
                success = save_expense(description, amount, category)
                
                if success:
                    response_msg = f"✅ Saved: {description} - ${amount:.2f}\n📁 Category: {category}"
                else:
                    response_msg = "❌ Error saving to Google Sheets. Check configuration."
            except Exception as e:
                response_msg = f"❌ Error: {str(e)}"
        else:
            response_msg = "❌ I couldn't understand that.\n\nTry:\n• 'Lunch 15.50'\n• 'help' for commands"
    
    # Send response back via Twilio
    from twilio.twiml.messaging_response import MessagingResponse
    resp = MessagingResponse()
    resp.message(response_msg)
    
    return str(resp)

@app.route('/', methods=['GET'])
def home():
    return """
    <h1>WhatsApp Expense Tracker 🚀</h1>
    <p>Status: Running</p>
    <p>✅ Google AI (Gemini)</p>
    <p>✅ Google Sheets Database</p>
    <p>✅ Twilio WhatsApp</p>
    """

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
