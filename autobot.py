import asyncio
import os
import time
import yaml
import requests
import bittensor as bt
from bittensor.core.async_subtensor import get_async_subtensor
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box
from tenacity import retry, stop_after_attempt, wait_exponential

console = Console()
bt.trace()

# Global history variables for the /history command.
last_history_snapshot = None
accumulated_history = []

# List of endpoints to try
ENDPOINTS = [
    "finney", 
    "subvortex",
    "archive",
]

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10)
)
async def get_and_test_subtensor(endpoint=None):
    """
    Get an initialized subtensor connection and test it works.
    Retries on failure, cycling through different endpoints.
    """
    sub = bt.async_subtensor(endpoint)
    await sub.initialize()
    
    # Test the connection works
    try:
        await sub.get_current_block()
        return sub
    except Exception as e:
        await sub.close()
        raise e

async def get_working_subtensor():
    """
    Try to get a working subtensor connection by cycling through endpoints.
    """
    last_error = None
    for endpoint in ENDPOINTS:
        try:
            return await get_and_test_subtensor(endpoint)
        except Exception as e:
            last_error = e
            continue
    raise Exception(f"Failed to connect to any endpoints. Last error: {last_error}")

# --- Configuration Loader ---

def load_config(config_file="config.yaml"):
    """Load configuration from a YAML file."""
    with open(config_file, "r") as f:
        config = yaml.safe_load(f)
    # Convert preferences keys to strings (we use string keys in our lookups)
    config["preferences"] = {str(k): v for k, v in config.get("preferences", {}).items()}
    # Ensure we have a paused flag (default False)
    if "paused" not in config:
        config["paused"] = False
    return type('Config', (), config)()

# --- Telegram Notification & Command Handling ---

def send_telegram_message(message, config, chat_id=None):
    """Send a Telegram message using the bot API with Markdown formatting."""
    telegram_token = config.telegram_token
    # Use provided chat_id (for command replies) or fallback to config's chat id.
    chat_id = chat_id or config.telegram_chat_id
    if not telegram_token or not chat_id:
        return  # Nothing to do if not configured
    url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
    data = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, data=data)
        response.raise_for_status()
    except Exception as e:
        console.print(Panel(f"Error sending telegram message: {e}", title="Telegram Error"))

def build_telegram_update_message(purchase_history, summary, current_block):
    """Build a nicely formatted Markdown message for Telegram updates."""
    lines = []
    lines.append(f"*Staking Update*")
    lines.append(f"Block: `{current_block}`")
    lines.append("")
    lines.append(f"*Summary:*")
    lines.append(f"{summary}")
    if purchase_history:
        lines.append("")
        lines.append(f"*Purchase History:*")
        for event in purchase_history:
            lines.append(
                f"- Block `{event['block']}`: Subnet `{event['netuid']}` (_{event['subnet_name']}_) staked `{event['stake_amount']:.4f}` TAO (score: `{event['score']:.4f}`, mult: `{event['pref_multiplier']:.2f}`)"
            )
    return "\n".join(lines)

async def poll_telegram_updates(wallet, config):
    """
    Periodically poll Telegram for incoming commands.
    Supported commands:
      /info <netuid>, /boost <netuid>, /slash <netuid>,
      /exclude <netuid>, /sell <netuid> <amount>, /buy <netuid> <amount>,
      /amount <value>, /balance, /history, /pause, /start
    """
    telegram_token = config.telegram_token
    if not telegram_token:
        return
    offset = 0
    poll_interval = 3  # seconds between polls
    while True:
        url = f"https://api.telegram.org/bot{telegram_token}/getUpdates?offset={offset}"
        try:
            response = await asyncio.to_thread(requests.get, url)
            data = response.json()
        except Exception as e:
            console.print(Panel(f"Error polling telegram: {e}", title="Telegram Poll Error"))
            await asyncio.sleep(poll_interval)
            continue

        for update in data.get("result", []):
            offset = max(offset, update["update_id"] + 1)
            # Check for both "message" and "channel_post"
            message = update.get("message") or update.get("channel_post")
            if message and "text" in message:
                await handle_telegram_command(message, wallet, config)
        await asyncio.sleep(poll_interval)

async def handle_telegram_command(message, wallet, config):
    """
    Process a Telegram command.
    Supported commands:
      /info <netuid>       -- replies with subnet info and current preference.
      /boost <netuid>      -- increases the preference for that subnet by 0.1.
      /slash <netuid>      -- decreases the preference for that subnet by 0.1 (min 0.1).
      /exclude <netuid>    -- adds the subnet to the exclude list.
      /unstake <netuid> <amount>   -- sells (unstakes) the specified amount from that subnet.
      /stake <netuid> <amount>    -- buys (stakes) the specified amount into that subnet.
      /amount <value>      -- sets the stake amount to the given value.
      /balance             -- returns a summary of your current portfolio.
      /history             -- returns a history summary since the last /history call.
      /pause               -- pauses the bot (no new stake allocations).
      /start               -- resumes staking.
    """
    bt.logging.trace(msg=f"{message}")
    text = message.get("text", "")
    chat_id = message["chat"]["id"]
    parts = text.strip().split()
    if len(parts) < 1:
        send_telegram_message("Invalid command.", config, chat_id)
        return

    cmd = parts[0].lower()

    if cmd == "/pause":
        config.paused = True
        send_telegram_message("Bot is now paused. No new staking actions will be performed.", config, chat_id)
        return

    if cmd == "/start":
        config.paused = False
        send_telegram_message("Bot has resumed staking.", config, chat_id)
        return

    if cmd == "/balance":
        # Create a temporary subtensor connection and retrieve portfolio info.
        try:
            sub = await get_working_subtensor()
        except Exception as e:
            send_telegram_message(f"Error initializing subtensor connection: {e}", config, chat_id)
            return
        try:
            current_block = await sub.get_current_block()
            stake_info = await sub.get_stake_for_coldkey(coldkey_ss58=wallet.coldkeypub.ss58_address)
            wallet_balance = float(await sub.get_balance(wallet.coldkey.ss58_address))
        except Exception as e:
            send_telegram_message(f"Error retrieving balance info: {e}", config, chat_id)
            await sub.close()
            return
        msg_lines = []
        msg_lines.append("*Portfolio Balance Info:*")
        msg_lines.append(f"• Wallet Balance: `{wallet_balance:.4f}` TAO")
        msg_lines.append(f"• Current Block: `{current_block}`")
        msg_lines.append("")
        if stake_info:
            msg_lines.append("*Staked Amounts by Subnet:*")
            for stake in stake_info:
                try:
                    stake_amt = float(stake.stake)
                except Exception:
                    stake_amt = 0.0
                if stake_amt > 0:
                    msg_lines.append(f"• Subnet `{stake.netuid}`: `{stake_amt:.4f}` {stake_info.symbol}")
        else:
            msg_lines.append("No stakes found.")
        reply = "\n".join(msg_lines)
        send_telegram_message(reply, config, chat_id)
        await sub.close()
        return

    if cmd == "/history":
        # Build a portfolio snapshot and compare to the last snapshot.
        try:
            sub = await get_working_subtensor()
        except Exception as e:
            send_telegram_message(f"Error initializing subtensor connection: {e}", config, chat_id)
            return
        try:
            current_block = await sub.get_current_block()
            subnets = await sub.all_subnets()
            stake_info = await sub.get_stake_for_coldkey(coldkey_ss58=wallet.coldkeypub.ss58_address)
            wallet_balance = float(await sub.get_balance(wallet.coldkey.ss58_address))
        except Exception as e:
            send_telegram_message(f"Error retrieving portfolio info: {e}", config, chat_id)
            await sub.close()
            return
        # Build mapping of subnet info: netuid -> {price, name}
        subnet_info = {}
        for s in subnets:
            if s.netuid != 0:
                subnet_info[str(s.netuid)] = {"price": float(s.price), "name": s.subnet_name}
        # Build current stakes dictionary: netuid (string) -> staked amount
        current_stakes = {}
        for stake in stake_info:
            netuid_str = str(stake.netuid)
            try:
                amt = float(stake.stake)
            except Exception:
                amt = 0.0
            current_stakes[netuid_str] = amt
        current_total_stake = sum(current_stakes.values())
        # Compute current total stake value using subnet prices
        current_total_stake_value = 0.0
        for netuid, amt in current_stakes.items():
            price = subnet_info.get(netuid, {}).get("price", 0.0)
            current_total_stake_value += amt * price

        current_time = time.time()
        current_snapshot = {
            "time": current_time,
            "block": current_block,
            "wallet_balance": wallet_balance,
            "stake_value": current_total_stake_value,
            "stakes": current_stakes
        }
        global last_history_snapshot, accumulated_history
        if last_history_snapshot is None:
            last_history_snapshot = current_snapshot
            send_telegram_message("History snapshot created. No previous history available.", config, chat_id)
        else:
            time_diff = current_time - last_history_snapshot["time"]
            block_diff = current_block - last_history_snapshot["block"]
            # "Amount" staked in this period from accumulated_history
            amount_staked = sum(event["stake_amount"] for event in accumulated_history)
            previous_total_stake = sum(last_history_snapshot["stakes"].values())
            stake_diff = current_total_stake - previous_total_stake
            previous_value = last_history_snapshot["wallet_balance"] + last_history_snapshot["stake_value"]
            current_value = wallet_balance + current_total_stake_value
            pnl = current_value - previous_value
            increases_lines = []
            for netuid, current_amt in current_stakes.items():
                previous_amt = last_history_snapshot["stakes"].get(netuid, 0.0)
                diff = current_amt - previous_amt
                if diff > 0:
                    name = subnet_info.get(netuid, {}).get("name", "")
                    increases_lines.append(f"     {netuid} ({name}): +{diff:.4f}")
            increases_str = "\n".join(increases_lines) if increases_lines else "None"
            history_msg = (
                f"*History Summary:*\n"
                f"Time: {time_diff:.2f} seconds\n"
                f"Blocks: {block_diff}\n"
                f"Amount Staked: {amount_staked:.4f} TAO\n"
                f"Total Stake Added: {stake_diff:.4f} TAO\n"
                f"PNL: {pnl:.4f} TAO\n"
                f"Increase:\n{increases_str}"
            )
            send_telegram_message(history_msg, config, chat_id)
            last_history_snapshot = current_snapshot
            accumulated_history.clear()
        await sub.close()
        return

    # For commands that require a netuid, parse it.
    if cmd in ["/info", "/boost", "/slash", "/exclude", "/unstake", "/stake"]:
        if len(parts) < 2:
            send_telegram_message("Usage: Command requires a netuid.", config, chat_id)
            return
        try:
            netuid = int(parts[1])
        except ValueError:
            send_telegram_message("Usage: Command requires a numeric netuid.", config, chat_id)
            return

    if cmd == "/info":
        try:
            sub = await get_working_subtensor()
        except Exception as e:
            send_telegram_message(f"Error initializing subtensor connection: {e}", config, chat_id)
            return

        try:
            current_block = await sub.get_current_block()
            subnets = await sub.all_subnets()
        except Exception as e:
            send_telegram_message(f"Error retrieving subnet data: {e}", config, chat_id)
            await sub.close()
            return

        target = None
        for s in subnets:
            if s.netuid == netuid:
                target = s
                break

        if not target:
            send_telegram_message(f"Subnet {netuid} not found.", config, chat_id)
            await sub.close()
            return

        try:
            price = float(target.price)
        except Exception:
            price = 0.0

        try:
            stake_info = await sub.get_stake_for_coldkey(coldkey_ss58=wallet.coldkeypub.ss58_address)
        except Exception as e:
            send_telegram_message(f"Error retrieving stake info: {e}", config, chat_id)
            await sub.close()
            return

        my_stake = 0.0
        for stake in stake_info:
            if stake.netuid == netuid and stake.hotkey_ss58 == config.validator:
                try:
                    my_stake = float(stake.stake)
                except Exception:
                    my_stake = 0.0
                break

        current_pref = config.preferences.get(str(netuid), 1.0)
        reply = (
            f"*Subnet Info for {netuid} ({target.subnet_name}):*\n"
            f"• *Current Price:* `{price:.4f}` TAO\n"
            f"• *Your Stake:* `{my_stake:.4f}` {target.symbol}\n"
            f"• *Current Preference:* `{current_pref:.2f}`\n"
            f"• *Current Block:* `{current_block}`"
        )
        send_telegram_message(reply, config, chat_id)
        await sub.close()

    elif cmd == "/boost":
        current_pref = config.preferences.get(str(netuid), 1.0)
        new_pref = current_pref + 0.1
        config.preferences[str(netuid)] = new_pref
        send_telegram_message(f"New preference for subnet {netuid} is: `{new_pref:.2f}`", config, chat_id)

    elif cmd == "/slash":
        current_pref = config.preferences.get(str(netuid), 1.0)
        new_pref = current_pref - 0.1
        if new_pref < 0.1:
            new_pref = 0.1
        config.preferences[str(netuid)] = new_pref
        send_telegram_message(f"New preference for subnet {netuid} is: `{new_pref:.2f}`", config, chat_id)

    elif cmd == "/exclude":
        if netuid not in config.exclude_list:
            config.exclude_list.append(netuid)
            send_telegram_message(f"Subnet {netuid} has been added to the exclude list.", config, chat_id)
        else:
            send_telegram_message(f"Subnet {netuid} is already in the exclude list.", config, chat_id)

    elif cmd == "/unstake":
        if len(parts) < 3:
            send_telegram_message("Usage: /unstake <netuid> <amount>", config, chat_id)
            return
        try:
            amount = float(parts[2])
        except ValueError:
            send_telegram_message("Usage: /unstake <netuid> <amount> (amount must be a number)", config, chat_id)
            return
        try:
            sub = await get_working_subtensor()
        except Exception as e:
            send_telegram_message(f"Error initializing subtensor connection: {e}", config, chat_id)
            return
        try:
            await unstake_on_subnet(sub, wallet, config.validator, netuid, amount)
            send_telegram_message(f"Unstaked {amount:.4f} from subnet {netuid}.", config, chat_id)
        except Exception as e:
            send_telegram_message(f"Error unstaking from subnet {netuid}: {e}", config, chat_id)
        await sub.close()

    elif cmd == "/stake":
        if len(parts) < 3:
            send_telegram_message("Usage: /stake <netuid> <amount>", config, chat_id)
            return
        try:
            amount = float(parts[2])
        except ValueError:
            send_telegram_message("Usage: /stake <netuid> <amount> (amount must be a number)", config, chat_id)
            return
        try:
            sub = await get_working_subtensor()
        except Exception as e:
            send_telegram_message(f"Error initializing subtensor connection: {e}", config, chat_id)
            return
        try:
            await stake_on_subnet(sub, wallet, config.validator, netuid, amount)
            send_telegram_message(f"Staked {amount:.4f} TAO in subnet {netuid}.", config, chat_id)
        except Exception as e:
            send_telegram_message(f"Error staking in subnet {netuid}: {e}", config, chat_id)
        await sub.close()

    elif cmd == "/amount":
        try:
            new_amount = float(parts[1])
        except ValueError:
            send_telegram_message("Usage: /amount <value> (value must be a number)", config, chat_id)
            return
        config.stake_amount = new_amount
        send_telegram_message(f"New stake amount is: `{new_amount:.4f}` TAO", config, chat_id)

# --- New Helper for Selling (Unstaking) ---

async def unstake_on_subnet(sub, wallet, validator, netuid, amount):
    """Sell (unstake) a given TAO amount from a subnet."""
    return await sub.unstake(
        wallet=wallet,
        hotkey_ss58=validator,
        netuid=netuid,
        amount=bt.Balance.from_tao(amount),
        wait_for_inclusion=False,
        wait_for_finalization=False
    )

# --- Helper Functions for Main Processing ---

async def get_current_block(sub):
    return await sub.get_current_block()

async def get_all_subnets(sub):
    return await sub.all_subnets()

def select_best_subnet(subnets, exclude_list, preferences):
    """
    Select the subnet with the highest (preference-adjusted) positive score.
    In this version, the score is computed as tao_in_emission/price.
    """
    best_subnet = None
    best_score = 0.0
    for s in subnets:
        netuid = s.netuid
        if netuid == 0 or netuid in exclude_list:
            continue
        try:
            price = float(s.price)
        except Exception:
            continue
        if price <= 0:
            continue
        score = float(s.tao_in_emission) / float(price)
        pref_multiplier = preferences.get(str(netuid), 1.0)
        effective_score = score * pref_multiplier
        if effective_score > best_score:
            best_score = effective_score
            best_subnet = s
    return best_subnet, best_score

async def stake_on_subnet(sub, wallet, validator, netuid, stake_amount):
    return await sub.add_stake(
        wallet=wallet,
        hotkey_ss58=validator,
        netuid=netuid,
        amount=bt.Balance.from_tao(stake_amount),
        wait_for_inclusion=False,
        wait_for_finalization=False
    )

async def get_stake_info(sub, wallet, validator):
    stakes = await sub.get_stake_for_coldkey(coldkey_ss58=wallet.coldkeypub.ss58_address)
    return {stake.netuid: stake for stake in stakes if stake.hotkey_ss58 == validator}

async def get_wallet_balance(sub, wallet):
    return float(await sub.get_balance(wallet.coldkey.ss58_address))

def build_display_table(subnets, current_block, stake_info, exclude_list, preferences, chosen_netuid, stake_action, wallet_balance):
    """
    Build a display table with subnet details including price, score,
    preference multiplier, stake, and action taken.
    """
    table = Table(title="Subnet Overview", box=box.SIMPLE_HEAVY, header_style="bold white on dark_blue")
    table.add_column("Subnet", style="bright_cyan", justify="right")
    table.add_column("Name", style="bright_cyan", justify="left")
    table.add_column("Price", style="green", justify="right")
    table.add_column("Score", style="yellow", justify="right")
    table.add_column("Pref Mult", style="cyan", justify="right")
    table.add_column("Stake", style="red", justify="right")
    table.add_column("Action", style="white", justify="left")
    
    total_stake = 0.0
    total_stake_value = 0.0
    total_price = 0.0

    for s in subnets:
        netuid = s.netuid
        if netuid == 0:
            continue
        try:
            price = float(s.price)
        except Exception:
            price = 0.0
        if price <= 0:
            continue
        total_price += price
        score = float(s.tao_in_emission) / price
        pref_multiplier = preferences.get(str(netuid), 1.0)
        stake_amt = 0.0
        if netuid in stake_info:
            try:
                stake_amt = float(stake_info[netuid].stake)
            except Exception:
                stake_amt = 0.0
        total_stake += stake_amt
        total_stake_value += stake_amt * price
        if netuid in exclude_list:
            action_str = "Excluded"
        elif chosen_netuid == netuid:
            action_str = stake_action
        else:
            action_str = ""
        table.add_row(
            str(netuid),
            s.subnet_name,
            f"{float(price):.4f}",
            f"{float(s.tao_in_emission):.4f}",
            f"{float(score):.4f}",
            f"{pref_multiplier:.2f}",
            f"{float(stake_amt):.4f}",
            action_str
        )
    summary = (
        f"Wallet Balance: {wallet_balance:.4f} TAO | Total Subnet Prices: {total_price:.4f} | "
        f"Total Stake: {total_stake:.4f} | Total Stake Value: {total_stake_value:.4f} TAO"
    )
    return table, summary

# --- Main Processing Functions ---

async def process_block(sub, wallet, config, purchase_history):
    global accumulated_history
    exclude_list = config.exclude_list
    stake_amount = config.stake_amount
    validator = config.validator
    preferences = config.preferences

    # Get current block and all subnet data
    current_block = await get_current_block(sub)
    subnets = await get_all_subnets(sub)

    # Select the best subnet using a preference-adjusted score
    stake_info = await get_stake_info(sub, wallet, validator)
    chosen_subnet, best_score = select_best_subnet(subnets, exclude_list, preferences)
    
    stake_action = ""
    chosen_netuid = None
    # Only stake if not paused.
    if not config.paused and chosen_subnet and best_score > 0:
        chosen_netuid = chosen_subnet.netuid
        pref_multiplier = preferences.get(str(chosen_netuid), 1.0)
        actual_stake = stake_amount * pref_multiplier
        stake_action = f"Stake: {actual_stake:.4f} TAO (score: {best_score:.4f}, mult: {pref_multiplier:.2f})"
        try:
            await stake_on_subnet(sub, wallet, validator, chosen_netuid, actual_stake)
            # Record purchase event
            purchase_event = {
                "block": current_block,
                "netuid": chosen_subnet.netuid,
                "subnet_name": chosen_subnet.subnet_name,
                "stake_amount": actual_stake,
                "score": best_score,
                "pref_multiplier": pref_multiplier
            }
            purchase_history.append(purchase_event)
            accumulated_history.append(purchase_event)
        except Exception as e:
            stake_action = f"Stake error: {e}"
    elif config.paused:
        stake_action = "Paused"

    # Refresh stake info and wallet balance
    stake_info = await get_stake_info(sub, wallet, validator)
    wallet_balance = await get_wallet_balance(sub, wallet)
    
    # Build and print the display table
    table, summary = build_display_table(
        subnets, current_block, stake_info, exclude_list, preferences,
        chosen_netuid, stake_action, wallet_balance
    )
    console.clear()
    console.print(Panel(summary, title="Wallet & Stake Summary", style="bold white"))
    console.print(table)
    
    # Send a Telegram update if the current block is a multiple of the update interval.
    telegram_interval = config.telegram_update_interval
    if current_block % telegram_interval == 0 and purchase_history:
        telegram_message = build_telegram_update_message(purchase_history, summary, current_block)
        await asyncio.to_thread(send_telegram_message, telegram_message, config)
        purchase_history.clear()
    
    # Wait for the next block before processing again
    await sub.wait_for_block()
    return purchase_history

async def main_loop(wallet, config):
    sub = await get_working_subtensor()
    purchase_history = []
    while True:
        try:
            purchase_history = await process_block(sub, wallet, config, purchase_history)
        except Exception as e:
            console.print(Panel(f"Error in process block: {e}", title="Error"))
            break
    await sub.close()

# --- Main Entry Point ---

async def main():
    # Load configuration from config.yaml
    config = load_config("config.yaml")
    
    # Set up wallet (assumes WALLET_PASSWORD is in environment)
    wallet = bt.wallet(name=config.wallet)
    password = os.environ.get("WALLET_PASSWORD")
    wallet.coldkey_file.save_password_to_env(password)
    wallet.unlock_coldkey()
    
    # Start both the main loop and the telegram polling concurrently.
    await asyncio.gather(
        main_loop(wallet, config),
        poll_telegram_updates(wallet, config)
    )

if __name__ == "__main__":
    asyncio.run(main())
