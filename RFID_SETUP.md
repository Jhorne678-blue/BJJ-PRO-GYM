# RFID Card Scanning Setup Guide

## Current Status
❌ RFID hardware scanning is **NOT** currently implemented
✅ Manual check-in via dropdown works perfectly

## What You Need for RFID Scanning

### Hardware Required:
1. **RFID Reader** (USB or Serial)
   - Common options:
     - HID iClass reader
     - RFID-RC522 (cheap, works with Arduino)
     - Proxmark3 (advanced)
   - Price: $15-$200 depending on model

2. **RFID Cards/Fobs**
   - 125kHz or 13.56MHz cards
   - Must match your reader frequency
   - Price: $0.50-$5 per card

### Software Architecture Needed:

```
┌─────────────┐       ┌──────────────┐       ┌─────────────┐
│ RFID Reader │ USB   │ Bridge App   │ HTTP  │ BJJ Pro Gym │
│  Hardware   ├──────►│ (localhost)  ├──────►│   Backend   │
│             │       │   Port 8888  │       │  Port 8000  │
└─────────────┘       └──────────────┘       └─────────────┘
```

### Implementation Steps:

#### 1. Create RFID Bridge Application (Python)
```python
# rfid_bridge.py
import serial
import requests
from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# Configure your RFID reader
RFID_PORT = 'COM3'  # Windows
# RFID_PORT = '/dev/ttyUSB0'  # Linux
# RFID_PORT = '/dev/cu.usbserial'  # Mac

ser = serial.Serial(RFID_PORT, 9600)

@app.route('/scan', methods=['GET'])
def scan_card():
    """Wait for card scan and return card number"""
    if ser.in_waiting:
        card_number = ser.readline().decode('utf-8').strip()
        return jsonify({'card_number': card_number, 'status': 'success'})
    return jsonify({'status': 'waiting'})

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'connected'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8888)
```

#### 2. Update Backend API (Already has endpoint!)
```python
# main.py already has this:
@app.post("/api/attendance/checkin")
async def check_in(checkin: CheckInRequest, token_data: dict = Depends(verify_token)):
    # Supports card_number parameter
    if checkin.card_number:
        # Find student by card number
        cursor.execute('''
            SELECT id, name, member_id, card_number FROM students
            WHERE gym_id = ? AND card_number = ?
        ''', (gym_id, checkin.card_number))
```

#### 3. Update Frontend (Add RFID Scanner Component)
```javascript
// Add to index.html
const RFIDScanner = () => {
    const [scanning, setScanning] = useState(false);
    const [lastScan, setLastScan] = useState(null);

    useEffect(() => {
        if (!scanning) return;

        const interval = setInterval(async () => {
            try {
                // Check if bridge is running
                const bridgeResponse = await fetch('http://localhost:8888/scan');
                const data = await bridgeResponse.json();

                if (data.status === 'success' && data.card_number) {
                    // Send check-in to backend
                    await apiCall('/api/attendance/checkin', {
                        method: 'POST',
                        body: JSON.stringify({ card_number: data.card_number })
                    });
                    setLastScan(data.card_number);
                }
            } catch (err) {
                console.log('RFID bridge not connected');
            }
        }, 500); // Poll every 500ms

        return () => clearInterval(interval);
    }, [scanning]);

    return (
        <div>
            <button onClick={() => setScanning(!scanning)}>
                {scanning ? 'Stop Scanning' : 'Start RFID Scanner'}
            </button>
            {lastScan && <div>Last scan: {lastScan}</div>}
        </div>
    );
};
```

#### 4. Install Dependencies for Bridge
```bash
pip install pyserial flask flask-cors
```

#### 5. Run the System
```bash
# Terminal 1: Start backend
python main.py

# Terminal 2: Start RFID bridge
python rfid_bridge.py

# Terminal 3: Open frontend
# Open index.html in browser
```

### Testing Without Hardware:
```python
# test_rfid_bridge.py - Simulates card scans
from flask import Flask, jsonify
from flask_cors import CORS
import time
import random

app = Flask(__name__)
CORS(app)

# Fake card numbers for testing
DEMO_CARDS = ['CARD1001', 'CARD1002', 'CARD1003']

@app.route('/scan', methods=['GET'])
def scan_card():
    """Simulate random card scans"""
    if random.random() > 0.7:  # 30% chance of scan
        card = random.choice(DEMO_CARDS)
        return jsonify({'card_number': card, 'status': 'success'})
    return jsonify({'status': 'waiting'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8888)
```

## Quick Start (Without Hardware)

1. **Use manual check-in** (already works)
   - Go to Attendance tab
   - Select student from dropdown
   - Click "Check In"

2. **Test with simulated bridge**
   ```bash
   python test_rfid_bridge.py
   # This will simulate random card scans
   ```

## Cost Estimate

| Item | Price | Notes |
|------|-------|-------|
| Basic RFID Reader | $15-50 | USB, 125kHz |
| RFID Cards (100 pack) | $30-50 | Blank cards |
| Development Time | 4-8 hours | Coding the bridge |
| **Total** | **$45-$100** | Plus your time |

## Alternative: QR Code Check-In

Instead of RFID, you could use **QR codes** (FREE):
- Generate QR code for each student's member ID
- Use smartphone camera to scan
- Much cheaper ($0 cost)
- Easier to implement

Would you like me to implement QR code scanning instead?

## Questions?

- Do you already have RFID hardware?
- What type of reader do you have?
- Would QR codes work better for you?

---

**Summary:** RFID requires additional hardware and software bridge. Manual check-in works perfectly right now. QR codes might be a better alternative.
