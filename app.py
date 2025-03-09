import streamlit as st
import pandas as pd
import requests
import json
import time
from datetime import datetime, timedelta

# Configure the Streamlit page.
st.set_page_config(page_title="Token Total Supply Dashboard", layout="wide")
st.title("Token Total Supply Dashboard")

# QuickNode URL input
QUICKNODE_URL = st.text_input("Enter your QuickNode URL:", "")

# Add API key input
SEITRACE_API_KEY = st.text_input("Enter your Seitrace API Key:", type="password")

# Token input section
st.subheader("Token Configuration")
st.write("Add one or more tokens to track")

# Initialize session state for tokens if it doesn't exist
if 'tokens' not in st.session_state:
    st.session_state.tokens = []

def call_rpc(method, params, retries=3, delay=1):
    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "id": 1,
    }
    for attempt in range(retries):
        try:
            response = requests.post(
                QUICKNODE_URL,
                headers={"Content-Type": "application/json"},
                data=json.dumps(payload),
                timeout=10,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            st.write(f"RPC call failed (attempt {attempt+1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(delay)
    return None

# Form for adding new tokens
with st.form("add_token_form"):
    token_name = st.text_input("Token Name (e.g., USDC)")
    token_contract = st.text_input("Token Contract Address")
    
    if st.form_submit_button("Add Token"):
        # Fetch decimals from the contract
        decimals_payload = {
            "to": token_contract,
            "data": "0x313ce567"  # Function signature for decimals()
        }
        decimals_response = call_rpc("eth_call", [decimals_payload, "latest"])
        
        if decimals_response and "result" in decimals_response:
            try:
                token_decimals = int(decimals_response["result"], 16)
                new_token = {
                    "name": token_name,
                    "contract": token_contract,
                    "decimals": token_decimals
                }
                st.session_state.tokens.append(new_token)
                st.success(f"Token added successfully with {token_decimals} decimals")
            except (ValueError, TypeError):
                st.error("Failed to parse token decimals")
        else:
            st.error("Failed to fetch token decimals")

# Display current tokens
st.write("Current Tokens:")
for idx, token in enumerate(st.session_state.tokens):
    st.write(f"{token['name']}: {token['contract']} (Decimals: {token['decimals']})")
    if st.button(f"Remove {token['name']}", key=f"remove_{idx}"):
        st.session_state.tokens.pop(idx)
        st.experimental_rerun()

def call_rpc_batch(batch_payload, retries=3, delay=1):
    for attempt in range(retries):
        try:
            response = requests.post(
                QUICKNODE_URL,
                headers={"Content-Type": "application/json"},
                data=json.dumps(batch_payload),
                timeout=10,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            st.write(f"Batch RPC call failed (attempt {attempt+1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(delay)
    return None

def get_closest_block_timestamp(target_date):
    latest_block = call_rpc("eth_blockNumber", [])
    if not latest_block or "result" not in latest_block:
        st.write("Error fetching the latest block number.")
        return None, None

    latest_block_number = int(latest_block["result"], 16)
    low, high = 0, latest_block_number
    chosen_block, chosen_datetime = None, None

    while low <= high:
        mid = (low + high) // 2
        block_data = call_rpc("eth_getBlockByNumber", [hex(mid), False])
        if not block_data or "result" not in block_data:
            st.write(f"Error fetching block {mid}")
            return None, None

        block_timestamp = int(block_data["result"]["timestamp"], 16)
        block_datetime = datetime.utcfromtimestamp(block_timestamp)
        block_date = block_datetime.date()

        if block_date == target_date:
            return mid, block_datetime
        elif block_date < target_date:
            low = mid + 1
            chosen_block, chosen_datetime = mid, block_datetime
        else:
            high = mid - 1

    return chosen_block, chosen_datetime

def get_holder_count(contract_address, chain_id="pacific-1"):
    """Fetch token holder count from Seitrace API"""
    if not SEITRACE_API_KEY:
        return None
        
    session = requests.Session()
    session.headers.update({
        "accept": "application/json",
        "x-api-key": SEITRACE_API_KEY,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/97.0.4692.71 Safari/537.36",
    })
    
    url = "https://seitrace.com/insights/api/v2/token/erc20"
    params = {
        "chain_id": chain_id,
        "contract_address": contract_address,
    }
    
    try:
        response = session.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        holder_count = data.get('token_holder_count')
        # Convert string to integer if it exists
        return int(holder_count) if holder_count is not None else None
    except (requests.exceptions.RequestException, ValueError) as e:
        st.error(f"Failed to fetch holder count: {str(e)}")
        return None

def get_token_total_supplies(block_number):
    batch_payload = []
    id_to_token = {}
    for i, token in enumerate(st.session_state.tokens):
        req = {
            "jsonrpc": "2.0",
            "id": i,
            "method": "eth_call",
            "params": [
                {
                    "to": token["contract"],
                    "data": "0x18160ddd"
                },
                hex(block_number)
            ]
        }
        batch_payload.append(req)
        id_to_token[i] = token

    responses = call_rpc_batch(batch_payload)
    token_supplies = {}
    
    if responses is None:
        for token in st.session_state.tokens:
            token_supplies[token["name"]] = None
        return token_supplies

    for resp in responses:
        resp_id = resp.get("id")
        result_hex = resp.get("result")
        token_info = id_to_token.get(resp_id)
        if token_info is None:
            continue
            
        # Get supply
        if result_hex in (None, "0x", "0x0"):
            supply = 0
        else:
            try:
                supply = int(result_hex, 16)
            except ValueError:
                supply = None
                
        token_supplies[token_info["name"]] = supply

    return token_supplies

def get_token_total_supplies_with_retries(block_number, max_retries=3, delay=1):
    for attempt in range(max_retries):
        supplies = get_token_total_supplies(block_number)
        if supplies and all(supply is not None for supply in supplies.values()):
            return supplies
        time.sleep(delay)
    return None

def get_data_for_date_range(start_date, end_date):
    data_rows = []
    current_date = start_date
    holder_data = {}  # Store holder counts separately
    today = datetime.utcnow().date()

    while current_date <= end_date:
        st.write(f"Fetching data for {current_date}...")
        
        # If the current date is today, use the latest block instead of searching for a specific timestamp
        if current_date == today:
            latest_block_response = call_rpc("eth_blockNumber", [])
            if not latest_block_response or "result" not in latest_block_response:
                st.write("Error fetching the latest block number, skipping...")
                current_date += timedelta(days=1)
                continue
                
            block_number = int(latest_block_response["result"], 16)
            block_data = call_rpc("eth_getBlockByNumber", [hex(block_number), False])
            if not block_data or "result" not in block_data:
                st.write(f"Error fetching latest block data, skipping...")
                current_date += timedelta(days=1)
                continue
                
            block_timestamp = int(block_data["result"]["timestamp"], 16)
            block_datetime = datetime.utcfromtimestamp(block_timestamp)
            st.write(f"Using latest block {block_number} for today ({block_datetime})")
        else:
            # For past dates, use the existing method to find the closest block
            block_number, block_datetime = get_closest_block_timestamp(current_date)
            if block_number is None:
                st.write(f"No block found for {current_date}, skipping...")
                current_date += timedelta(days=1)
                continue

        # Only get supply data in the time series
        token_supplies = get_token_total_supplies(block_number)
        
        row = {"date": current_date.strftime('%Y-%m-%d'), "block": block_number}
        for token in st.session_state.tokens:
            raw_supply = token_supplies.get(token["name"])
            if raw_supply is not None:
                row[f"{token['name']}_supply"] = raw_supply / (10 ** token["decimals"])
            else:
                row[f"{token['name']}_supply"] = None

        data_rows.append(row)
        current_date += timedelta(days=1)

    # Get current holder counts once
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')
    for token in st.session_state.tokens:
        holder_count = get_holder_count(token["contract"])
        holder_data[token["name"]] = {
            "count": holder_count,
            "timestamp": current_time
        }

    return data_rows, holder_data

# Only show the date selection and fetch button if we have both a QuickNode URL and at least one token
if QUICKNODE_URL and SEITRACE_API_KEY and st.session_state.tokens:
    st.subheader("Select Date Range")
    
    # Set min and max dates
    min_date = datetime(2024, 6, 1).date()
    max_date = datetime.utcnow().date()
    
    # Date selection
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input(
            "Start Date",
            min_value=min_date,
            max_value=max_date,
            value=max_date - timedelta(days=30)
        )
    with col2:
        end_date = st.date_input(
            "End Date",
            min_value=min_date,
            max_value=max_date,
            value=max_date
        )
    
    if st.button("Fetch Token Supply Data"):
        if start_date > end_date:
            st.error("Start date must be before end date")
        else:
            with st.spinner("Fetching data..."):
                data, holder_data = get_data_for_date_range(start_date, end_date)

            if data:
                df = pd.DataFrame(data)
                df['date'] = pd.to_datetime(df['date'])
                df.sort_values('date', inplace=True)
                df.set_index('date', inplace=True)
                st.success("Data fetched successfully!")
                
                # Display supply data
                st.subheader("Total Supply Over Time")
                st.write(df)

                # Display current holder counts
                st.subheader("Token Holder Counts")
                for token in st.session_state.tokens:
                    token_name = token["name"]
                    holder_info = holder_data.get(token_name, {})
                    
                    if holder_info.get("count") is not None:
                        st.metric(
                            label=f"{token_name} Holders",
                            value=f"{holder_info['count']:,}",
                            help=f"Last updated: {holder_info['timestamp']}"
                        )
                    else:
                        st.write(f"No holder data available for {token_name}")

                # Display supply charts
                for token in st.session_state.tokens:
                    token_name = token["name"]
                    st.subheader(f"{token_name} Supply Over Time")
                    if f"{token_name}_supply" in df.columns:
                        st.line_chart(df[f"{token_name}_supply"])
                    else:
                        st.write("No supply data available.")
            else:
                st.error("No data was fetched.")
else:
    st.warning("Please enter your QuickNode URL, Seitrace API Key, and add at least one token to begin.")
