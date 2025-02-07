import requests
import json
import time
import random
from datetime import datetime, timedelta
from rich.console import Console
from rich.progress import Progress
from rich.table import Table
from itertools import cycle
from eth_account.messages import encode_structured_data
from eth_account import Account
from settings import DELAY_MIN, DELAY_MAX


# Initialize Rich console
console = Console()


# Function to get proxies from proxies.txt file
def get_proxies():
    with open("proxies.txt", "r") as file:
        proxies_list = [line.strip().split(":") for line in file]
    return proxies_list


# Function to configure a proxy for requests
def configure_proxy(proxy_info):
    proxy = {
        "http": f"socks5://{proxy_info[2]}:{proxy_info[3]}@{proxy_info[0]}:{proxy_info[1]}",
        "https": f"socks5://{proxy_info[2]}:{proxy_info[3]}@{proxy_info[0]}:{proxy_info[1]}"
    }
    return proxy


# Function to derive the wallet address from a private key
def derive_wallet_address(private_key):
    account = Account.from_key(private_key)
    return account.address


# Новый метод для подписания сообщения формата EIP-712
def sign_eip712_message(private_key, wallet_address, signature_deadline):
    data = {
        "types": {
            "CastVoteBySig": [
                {"name": "verifyingChainId", "type": "uint256"},
                {"name": "voter", "type": "address"},
                {"name": "yesVote", "type": "bool"},
                {"name": "nonce", "type": "uint256"},
                {"name": "deadline", "type": "uint256"}
            ],
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "verifyingContract", "type": "address"}
            ]
        },
        "primaryType": "CastVoteBySig",
        "domain": {
            "name": "Reya",
            "version": "1",
            "verifyingContract": "0xce0c48b15a305f4675ced41ccebdc923d03b9b81"
        },
        "message": {
            "verifyingChainId": 1729,  # Число
            "voter": wallet_address,  # Адрес кошелька
            "yesVote": True,
            "nonce": 1,  # Число
            "deadline": signature_deadline  # Число
        }
    }

    # Кодирование данных EIP-712
    encoded_data = encode_structured_data(primitive=data)

    # Подписываем данные с использованием приватного ключа
    account = Account.from_key(private_key)
    signed_message = account.sign_message(encoded_data)

    return signed_message.signature.hex()


# Headers for the PUT request
headers = {
    "Host": "api.reya.xyz",
    "Origin": "https://app.reya.network",
    "Referer": "https://app.reya.network/"
}

# Read proxies and private keys
proxies_list = get_proxies()
proxy_cycle = cycle(proxies_list)  # Cycle through proxies for retries
current_proxy_info = next(proxy_cycle)  # Start with the first proxy

# Read private keys from keys.txt (replace this with the appropriate file for private keys)
with open("keys.txt", "r") as file:
    private_keys = [line.strip() for line in file]

# Progress bar setup
with Progress() as progress:
    task = progress.add_task("[cyan]Processing wallets...", total=len(private_keys))

    # Process each wallet
    for private_key in private_keys:
        success = False
        while not success:
            try:
                # Derive wallet address from private key
                wallet_address = derive_wallet_address(private_key)

                # Get a new proxy and configure it
                proxy = configure_proxy(current_proxy_info)

                # Step 1: Get latest product version (optional)
                tos_response = requests.get(
                    "https://api.reya.xyz/api/tos/latest-product-version/reya.network",
                    proxies=proxy
                )
                if tos_response.status_code == 200:
                    latest_version = tos_response.json()

                # Step 2: Get voting power and check if already voted
                vote_power_url = f"https://api.reya.xyz/api/vote/rnip3/user/{wallet_address}"
                vote_power_response = requests.get(vote_power_url, proxies=proxy)
                vote_power_data = vote_power_response.json()
                vote_power = vote_power_data['votingPower']
                has_voted = vote_power_data['hasVoted']

                # Пропуск кошелька, если votingPower = 0
                if vote_power == 0:
                    console.print(f"[yellow]Wallet {wallet_address} has no voting power (Voting Power = 0). Skipping.[/yellow]")
                    progress.advance(task)
                    success = True
                    continue

                # Пропуск кошелька, если уже проголосовал (hasVoted = True)
                if has_voted:
                    console.print(f"[yellow]Wallet {wallet_address} has already voted. Skipping.[/yellow]")
                    progress.advance(task)
                    success = True
                    continue

                # Step 3: Get signature
                signature_url = f"https://api.reya.xyz/api/owner/{wallet_address.lower()}/tos/get-signature/version/2"
                signature_response = requests.get(signature_url, proxies=proxy)
                signature_data = signature_response.json()
                signature = signature_data['signature']

                # New Step: Fetch contract details
                contract_details_url = "https://api.reya.xyz/api/vote/contract-details/rnip3"
                contract_details_response = requests.get(contract_details_url, proxies=proxy)
                contract_details = contract_details_response.json()

                # Step 4: Prepare and sign the vote
                # Calculate the signatureDeadline (7 days after the current voting time)
                current_time = datetime.now()  # Current time
                deadline_time = current_time + timedelta(days=7)  # Add 7 days
                signature_deadline = int(deadline_time.timestamp())  # Convert to UNIX timestamp

                # Sign the payload using the new EIP-712 signing method
                signed_signature = sign_eip712_message(private_key, wallet_address, signature_deadline)

                # Step 5: Submit the vote
                vote_url = "https://api.reya.xyz/api/vote/0xce0c48b15a305f4675ced41ccebdc923d03b9b81/vote"
                vote_payload = {
                    "voter": wallet_address,
                    "isYesVote": "yes",
                    "signature": signed_signature,
                    "signatureDeadline": signature_deadline  # Use the calculated deadline
                }

                # Submit the vote request
                vote_response = requests.put(vote_url, json=vote_payload, proxies=proxy, headers=headers)
                vote_result = vote_response.json()
                tx_hash = vote_result.get('txHash', None)

                # Log the results with Rich
                table = Table(title=f"Wallet {wallet_address} Processed", show_header=True, header_style="bold cyan")
                table.add_column("Wallet Address", style="dim", width=42)
                table.add_column("Voting Power", justify="right")
                table.add_column("Transaction Hash", style="magenta")

                table.add_row(wallet_address, str(vote_power), tx_hash or "N/A")
                console.print(table)

                # Update progress
                progress.advance(task)

                success = True  # Mark as successful to stop retrying

                # Delay between processing wallets (random between 100 to 200 seconds)
                delay = random.randint(DELAY_MIN, DELAY_MAX)
                console.print(f"Waiting for {delay} seconds before processing the next wallet...", style="dim")
                time.sleep(delay)

            except requests.exceptions.RequestException as e:
                # Handle proxy errors and switch to the next proxy in the list
                console.print(f"Error with proxy {current_proxy_info[0]} for wallet {wallet_address}: {e}",
                              style="bold red")
                current_proxy_info = next(proxy_cycle)  # Switch to the next proxy
                console.print(f"Switching to next proxy: {current_proxy_info[0]}", style="bold yellow")
