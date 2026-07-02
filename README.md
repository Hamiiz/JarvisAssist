# HmassAssistant 🤖

A fully-featured Telegram AI assistant powered by **Google Gemini 2.5 Flash** (Free Tier). 

It acts as your personal AI assistant, replying to users while you're away. It includes an interactive **Admin Management Panel** directly inside Telegram to toggle features, change personalities, add auto-replies, and view analytics.

## ✨ Features

- **Google Gemini 2.5 Flash**: Lightning fast, free-tier AI integration.
- **Admin Dashboard**: Manage everything via the `/admin` command.
- **Conversation Memory**: Remembers past messages per user using an async SQLite database.
- **Multi-lingual**: Fully understands English and Amharic (transliterated/Latin script), responding strictly in English.
- **Voice & Image Analysis**: Transcribes voice notes and describes images sent to it.
- **Auto FAQ**: Add custom keywords to trigger instant pre-written responses.
- **Anti-Spam & Rate Limiting**: Built-in protections to stay within Gemini's free tier limits.
- **Schedule**: Set active hours for the bot to reply.

---

## 🚀 Setup Instructions

### 1. Prerequisites

- Python 3.10 or higher.
- A Telegram Bot Token from [@BotFather](https://t.me/BotFather).
- A Gemini API Key from [Google AI Studio](https://aistudio.google.com/) (Ensure billing is NOT enabled to stay on the free tier).
- Your Telegram User ID from [@userinfobot](https://t.me/userinfobot) (to grant yourself admin access).

### 2. Installation

1. Open a terminal in this project directory.
2. Create and activate a virtual environment:
   ```bash
   python -m venv venv
   # On Windows:
   venv\Scripts\activate
   # On Mac/Linux:
   source venv/bin/activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

### 3. Configuration

1. Copy the example `.env` file:
   ```bash
   copy .env.example .env
   ```
2. Open `.env` in a text editor and fill in your keys:
   ```env
   TELEGRAM_TOKEN=your_bot_token_here
   GEMINI_API_KEY=your_gemini_api_key_here
   ADMIN_IDS=your_numeric_user_id_here
   ```

### 4. Running the Bot (Standard)

Run the following command to start the bot:

```bash
python -m bot.main
```

### 5. Running with Docker (Recommended for Servers)

We provide a complete Docker setup for seamless, reliable deployment.

1. Install [Docker](https://docs.docker.com/get-docker/) and Docker Compose.
2. Ensure your `.env` file is properly configured.
3. Build and start the bot in the background:
   ```bash
   docker compose up -d
   ```
4. To view the logs, run:
   ```bash
   docker logs -f hmassassistant_bot
   ```

The database is mounted as a volume (`/data`), ensuring your conversation memory persists across restarts and updates!

---

## 🚀 Server Deployment (CI/CD & Systemd)

- **CI/CD**: A GitHub Actions workflow (`ci.yml`) is included to automatically run syntax linting via `flake8` whenever you push code.
- **Updates**: A convenient `deploy.sh` script is provided. Running `./deploy.sh` will `git pull` the latest code, rebuild the Docker container, and clean up old images.
- **Systemd**: If you prefer not to use Docker, we've included `hmassassistant.service`. You can copy this to `/etc/systemd/system/` to manage the bot as a native Linux background service.

---

## 🛠️ Bot Commands

Send these to your bot in Telegram:

- `/start` — Start the bot.
- `/help` — List available commands.
- `/clear` — Wipe your personal conversation history.
- `/status` — View bot health, uptime, and global stats.
- `/admin` — Open the inline management dashboard **(Admin only)**.
- `/cancel` — Cancel any pending admin input.

---

## ⚙️ The Admin Panel

Using `/admin`, you can:
- Change the bot's **Persona** (Friendly, Professional, Witty, etc. or set a Custom Prompt).
- Toggle **Features** (Voice messages, Image analysis, Typing simulation).
- Add **FAQ** triggers.
- View **Analytics** (Messages received, AI responses).
- Manage **Users** (Clear their history, Block/Unblock).
- Send a global **Broadcast** message to all users.
