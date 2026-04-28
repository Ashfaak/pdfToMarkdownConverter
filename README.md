# PDF to Markdown Converter with AI Image Descriptions

This Python script automates the conversion of technical PDFs (including formulas) into high-quality Markdown (`.md`) format. It integrates with an AI vision-language model via an Ollama API to generate detailed descriptions of visual elements such as circuit diagrams, graphs, and tables.

## Features

- **Automated PDF to Markdown**: Uses [Docling](https://github.com/docling-project/docling) to accurately extract text, tables, and document structure.
- **AI-Powered Image Descriptions**: Extracts images/figures from the PDF and sends them to a vision-language model (via Ollama) to generate concise, technical descriptions.
- **Resumable Processing & Caching**: Processes PDFs page-by-page and caches successful outputs. If interrupted (e.g., by a network error or user cancellation), rerunning the script will pick up exactly where it left off without re-processing already completed pages or images.
- **Smart Formula Enrichment**: Performs a fast initial pass, and if undecoded formulas are detected, automatically re-runs the page with advanced formula enrichment enabled.
- **Error Handling & Retries**: Includes built-in API retries and graceful failure handling. Failed pages are marked and retried on subsequent runs.
- **Memory Efficient**: Loads, processes, and unloads pages individually to prevent memory exhaustion on large documents.

## Directory Structure

The script relies on the following directory structure relative to where it is executed:

```text
├── convert_with_ai.py      # The main script
├── Input/                  # Place your source PDFs here (subdirectories are supported)
├── Output/                 # Generated Markdown files will be saved here
└── .cache/                 # Auto-generated directory for storing page caches and image descriptions
```

## Prerequisites

### 1. Python Dependencies

Ensure you have Python installed, then install the required packages:

```bash
pip install requests pypdfium2 docling transformers
```

### 2. AI Model (Ollama)

The script uses a local proxy/Ollama server for image processing. By default, it expects:
- **API URL**: `http://localhost:11434/api/chat`
- **Model**: `gemma4:31b-cloud` (You can change this in the script configuration).

Make sure your Ollama instance is running and the specified vision model is pulled and available.

## Usage

1. Create an `Input` folder in the same directory as the script.
2. Place the PDF files you wish to convert into the `Input` folder. You can organize them into subfolders if needed.
3. Run the script:

```bash
python convert_with_ai.py
```

4. The script will output its progress in the console. Converted Markdown files will be generated in the `Output` folder, maintaining any subfolder structure from the `Input` directory.

## Configuration

You can easily tweak the script by modifying variables in the `# --- Configuration ---` section at the top of `convert_with_ai.py`:

- `OLLAMA_API_URL`: The endpoint for your AI model.
- `CLOUD_MODEL`: The specific model to use for generating image descriptions.
- `PROMPT`: The system prompt sent to the AI. Adjust this to focus on different types of visual content depending on your PDFs.

## How It Works

1. **Scanning**: The script scans the `Input` directory for `.pdf` files.
2. **Page Extraction**: Each PDF is split and processed page by page.
3. **Extraction & AI Vision**: Text and tables are extracted. Images larger than 150x150 pixels are sent to the AI model to generate a descriptive caption.
4. **Caching**: Descriptions and page markdown are cached in `.cache/`. 
5. **Assembly**: Once all pages of a PDF are successfully processed, they are combined into a single Markdown file in the `Output` directory.

Coded with Gemini 3.1 Pro (High), Claude Opus 4.6 (Thinking), and Gemini 3 Flash