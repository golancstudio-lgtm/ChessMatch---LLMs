# LLM Chess Match

A Python script that pits two Large Language Models against each other in a game of chess. Each LLM receives the current board position, responds with a move in PGN notation, and the script validates and applies the move—or asks for a retry if illegal.

## Features

- **Configurable LLM selection**: Choose which two LLMs play (ChatGPT, Gemini, Claude, Mistral, Cohere, Llama via Groq, Grok)
- **PGN move format**: LLMs respond with Standard Algebraic Notation (e.g., `e4`, `Nf3`, `O-O`)
- **Configurable retries**: Set how many times an LLM can retry after an illegal move
- **Automatic validation**: Uses `python-chess` to validate moves and apply them to the board

## Setup

1. **Create a virtual environment** (recommended):

   ```bash
   python -m venv venv
   venv\Scripts\activate   # Windows
   # or: source venv/bin/activate   # Linux/macOS
   ```

2. **Install dependencies**:

   ```bash
   pip install -r requirements.txt
   ```

3. **Set API keys** (choose one method):

   **Option A – `.env` file** (recommended, works from IDE/any shell):

   ```bash
   cp .env.example .env
   # Edit .env and add your keys
   ```

   **Option B – Environment variables**:

   - **OpenAI (ChatGPT)**: `OPENAI_API_KEY`
   - **Google (Gemini)**: `GEMINI_API_KEY` or `GOOGLE_API_KEY`
   - **Anthropic (Claude)**: `ANTHROPIC_API_KEY`
   - **Mistral**: `MISTRAL_API_KEY`
   - **Cohere**: `COHERE_API_KEY`
   - **Groq (Llama)**: `GROQ_API_KEY`
   - **xAI (Grok)**: `XAI_API_KEY`

## Usage

```bash
python main.py
```

The script will prompt you to:
1. Select the first LLM (White)
2. Select the second LLM (Black)
3. Set the maximum retries per illegal move
4. Run the game and display the board, moves, and outcome

## Project Structure

```
ChessMatch - LLMs/
├── main.py              # Entry point, console UI
├── requirements.txt
├── README.md
└── src/
    ├── __init__.py
    ├── chess_engine.py  # FEN, move validation, PGN handling
    ├── llm_adapters.py  # LLM API wrappers (ChatGPT, Gemini, Claude, Mistral, Cohere, Llama, Grok)
    ├── prompt_builder.py
    ├── response_parser.py
    └── game_loop.py
```

## License

MIT
