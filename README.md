# Bittensor Telegram DCA Bot

Interactive DCA script which uses a telegram frontend to pass commands.

---

## Getting Started

1. **Make a Telegram bot:**
   - Visit [@BotFather](https://t.me/BotFather) on Telegram
   - Send `/newbot` and follow the prompts to create your bot
   - Save the API token that BotFather gives you - this will be your `telegram_token` in config.yaml
   - Add your bot to a private Telegram channel/group with just you.
   - Send a test message to your bot
   - Visit https://api.telegram.org/bot<your_token>/getUpdates to get your chat_id.
   - Copy the "id" field from the JSON response - this will be your `telegram_chat_id` in config.yaml
   - Disable privacy mode by sending `/setprivacy` to BotFather and selecting your bot
   - Send another test message to verify everything is working

2. **Fill in config:**

    Create a `config.yaml` file in the project root. Below is an example configuration:

    ```yaml
        wallet: "<your wallet name>"
        stake_amount: 0.01        # Base TAO amount to stake per block.
        validator: "5F4tQyWrhfGVcNhoqeiNsR6KjD4wMZ2kfhLj4oHYuyHbZAc3" # OTF
        exclude_list: [..]     # Subnets to always exclude (never buy)
        base_alpha: 0.0003         # EMA smoothing factor.
        preferences:               # Preference multipliers per subnet.
            "4": 1.5
            "9": 2.0 # (2 here means this subnet gets a score multiple of 2x when choosing the best subnet to DCA into.
        telegram_token: "YOUR_TELEGRAM_BOT_TOKEN"  # ( see step 1.)
        telegram_chat_id: "-123456789"    # Your Telegram channel/group chat ID ( see step 1.)
        telegram_update_interval: 10      # Send periodic updates every 10 blocks.
    ```

    **Note:**  
    - The `preferences` keys must be strings.  
    - Update `telegram_token` and `telegram_chat_id` with your actual Telegram bot token and channel/group ID.


3. **Clone the Repository:**

   ```bash
   git clone https://github.com/unconst/DynamicBot.git
   cd DynamicBot
   ```

4. **Install Dependencies:**

    Use pip to install required packages:

    ```bash
    python3 -m venv venv
    source venv/bin/activate
    python3 -m pip install -r requirements.txt
    ```

5. **Install PM2:**

    For Ubuntu/Debian:
    ```bash
    apt update
    apt install -y nodejs npm
    npm install -g pm2
    ```

    For MacOS:
    ```bash
    # Install Homebrew if not already installed
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    
    # Install Node.js and npm
    brew install node
    
    # Install PM2 globally
    npm install -g pm2
    ```

6. **Run**

    Run the bot.
   
    ```bash
    export WALLET_PASSWORD='<YOUR WALLET PASSWORD HERE>'; pm2 delete autobot; pm2 start autobot.py --interpreter python3 --name autobot --cron-restart="0 * * * *"; pm2 logs autobot
    ```

7. **Use Telegram to interact**

Interact with the bot using Telegram. Below are the supported commands:

- **/pause**  
  _Description:_ Stops the bot staking into subnets.
  _Response:_ Confirms the bot has been paused.

- **/start**  
  _Description:_ Starts the bot staking into subnets
  _Response:_ Confirms the bot has been started.

- **/info `<netuid>`**  
  _Description:_ Returns information about the specified subnet, including:
  - Current price.
  - Your current stake.
  - Current preference multiplier.
  - Current block number.

- **/boost `<netuid>`**  
  _Description:_ Increases the preference multiplier for the specified subnet by 0.1.  
  _Response:_ New preference value.

- **/slash `<netuid>`**  
  _Description:_ Decreases the preference multiplier for the specified subnet by 0.1 (minimum 0.1).  
  _Response:_ New preference value.

- **/exclude `<netuid>`**  
  _Description:_ Adds the specified subnet to the exclude list (i.e., the bot will not stake in this subnet).  
  _Response:_ Confirmation message.

- **/sell `<netuid>` `<amount>`**  
  _Description:_ Sells (unstakes) the specified amount of TAO from the given subnet.  
  _Response:_ Confirmation of the unstake action.

- **/buy `<netuid>` `<amount>`**  
  _Description:_ Buys (stakes) the specified amount of TAO into the given subnet.  
  _Response:_ Confirmation of the stake action.

- **/amount `<value>`**  
  _Description:_ Sets the base stake amount used per block to the specified value.  
  _Response:_ New stake amount.

- **/balance**  
  _Description:_ Returns a summary of your current portfolio, including:
  - Wallet balance.
  - Current block.
  - Staked amounts per subnet.

- **/history**  
  _Description:_ Returns a detailed history summary since the last `/history` command. The summary includes:
  - Time elapsed and number of blocks since the last history snapshot.
  - Total amount staked during this period.
  - The difference in total stake.
  - PNL (difference between the previous and current combined wallet + stake value).
  - A per‑subnet breakdown of the increase in stake.
  _Response:_ A formatted message with all the details.


## Troubleshooting

- **Telegram Updates Not Received:**  
  - Ensure that your Telegram bot is added to your group or channel and that privacy mode is disabled if necessary.
  - Verify that your `telegram_token` and `telegram_chat_id` are correct.
  
- **Wallet Connection Issues:**  
  - Check that the `WALLET_PASSWORD` environment variable is set.
  - Verify the wallet name and file permissions.

- **Bittensor Connectivity:**  
  - Confirm that the endpoint in your configuration is correct and that you have a stable network connection.

---

## License

DYOR. NFA.

[MIT License](LICENSE)

---
