#!/usr/bin/env python3
"""
Harvest & Stash Utility  –  v1.1 (Using Responses API)

This script automates web_search_preview + file_search calls using the OpenAI
Responses API. It processes a YAML job list, respects simple rate limits, uploads
local files and manages temporary vector stores for file search, and stores every
response in a clean directory tree.

⚠️ Uses the Responses API (client.responses.create).
⚠️ File Search implementation creates a *temporary vector store for each file job*,
   which is inefficient for repeated searches on the same file. Consider
   pre-creating vector stores for better performance and cost-effectiveness.

USAGE
-----
$ export OPENAI_API_KEY=sk‑...
$ python harvest_toolgap.py jobs.yaml --outdir stash --sleep 0.3

jobs.yaml EXAMPLE
-----------------
- type: web
  query: "latest emperor penguin population study"
  out: penguin_pop.json
  model: gpt-4.1 # Example model supporting Responses API

- type: file
  file_path: "docs/my_document.pdf"   # Local file path required for this script
  query: "Summarize the document."
  out: my_document_summary.json
  model: gpt-4o-mini # Example model supporting Responses API + file search
"""

import argparse
import datetime as _dt
import json
import os
import pathlib
import sys
import time
from typing import Any, Dict, List, Optional
import requests
from io import BytesIO
from datetime import UTC # Import UTC if using Python 3.11+

from openai import OpenAI, APIError # Import APIError for specific handling
import yaml    # pip install pyyaml

# ----------------------------- CONFIG ------------------------------------ #
# Models need to support the Responses API (e.g., gpt-4.1, gpt-4o-mini)
DEFAULT_WEB_MODEL = "gpt-4.1" # Check OpenAI docs for current models
DEFAULT_FILE_MODEL = "gpt-4o-mini" # Check OpenAI docs for current models

# crude request‑count throttling (seconds between calls)
DEFAULT_SLEEP = 0.25
POLL_INTERVAL_S = 5 # How often to check vector store file processing status

# ------------------------------------------------------------------------- #

# Instantiate the client. It will automatically pick up the OPENAI_API_KEY environment variable.
client = OpenAI()

def _timestamp() -> str:
    # Use timezone-aware UTC time
    return _dt.datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


def smart_sleep(seconds: float, message=""):
    """Sleep helper with fractional seconds displayed."""
    if seconds <= 0:
        return
    prefix = f"⌛ {message} " if message else "⌛ Sleeping "
    mins, secs = divmod(seconds, 60)
    sys.stderr.write(f"{prefix}{int(mins):02d}:{secs:04.1f}\r")
    sys.stderr.flush()
    time.sleep(seconds)
    # Clear the line after sleeping
    sys.stderr.write(" " * (len(prefix) + 7) + "\r")
    sys.stderr.flush()

# --------------------------- OPENAI HELPERS ------------------------------ #

def run_web_search(query: str, model: str = DEFAULT_WEB_MODEL) -> Dict[str, Any]:
    """Perform a web_search tool call using the Responses API."""
    print(f"   Running web search...")
    response = client.responses.create(
        model=model,
        tools=[{"type": "web_search_preview"}], # Use the documented tool type
        input=query, # Use 'input' instead of 'messages'
    )
    # The primary text output is in response.output_text
    # The structured output (including calls and citations) is in response.output
    return {
        "query": query,
        "created": _timestamp(),
        "model": model,
        "output_text": response.output_text,
        "output_structured": [item.to_dict() for item in response.output], # Store structured output
    }


def setup_file_search_vector_store(path: pathlib.Path) -> str:
    """
    Uploads a file, creates a temporary vector store, adds the file to it,
    waits for processing, and returns the vector store ID.
    NOTE: Inefficient for repeated use. Consider managing persistent vector stores.
    """
    # 1. Upload the file
    print(f"   Uploading file: {path.name}...")
    try:
        with path.open("rb") as file_handle:
            file_obj = client.files.create(file=file_handle, purpose="assistants")
        print(f"   File uploaded: ID {file_obj.id}")
    except Exception as e:
        print(f"   Error uploading file {path.name}: {e}", file=sys.stderr)
        raise

    # 2. Create a temporary vector store
    vector_store_name = f"temp_vs_{path.stem}_{_timestamp()}"
    print(f"   Creating temporary vector store: {vector_store_name}...")
    try:
        vector_store = client.vector_stores.create(name=vector_store_name)
        print(f"   Vector store created: ID {vector_store.id}")
    except Exception as e:
        print(f"   Error creating vector store: {e}", file=sys.stderr)
        # Attempt cleanup: Delete uploaded file if store creation fails
        try: client.files.delete(file_obj.id)
        except: pass # Ignore cleanup errors
        raise

    # 3. Add the file to the vector store
    try:
        print(f"   Adding file {file_obj.id} to vector store {vector_store.id}...")
        vs_file = client.vector_stores.files.create(
            vector_store_id=vector_store.id,
            file_id=file_obj.id
        )
    except Exception as e:
        print(f"   Error adding file to vector store: {e}", file=sys.stderr)
        # Attempt cleanup
        try: client.vector_stores.delete(vector_store.id)
        except: pass
        try: client.files.delete(file_obj.id)
        except: pass
        raise

    # 4. Wait for the file to be processed
    print(f"   Waiting for file {vs_file.id} processing in vector store...")
    start_time = time.time()
    while True:
        try:
            vs_file = client.vector_stores.files.retrieve(
                vector_store_id=vector_store.id,
                file_id=file_obj.id
            )
            if vs_file.status == "completed":
                print(f"   File processing completed.")
                break
            elif vs_file.status == "failed":
                last_error = vs_file.last_error.message if vs_file.last_error else "Unknown error"
                raise RuntimeError(f"File processing failed: {last_error}")

            elapsed = time.time() - start_time
            smart_sleep(POLL_INTERVAL_S, f"Processing... (status: {vs_file.status}, elapsed: {elapsed:.0f}s)")

        except APIError as e:
            # Handle potential rate limits during polling
            print(f"   API Error while checking status: {e}. Retrying after delay...", file=sys.stderr)
            smart_sleep(POLL_INTERVAL_S * 2) # Longer sleep on error
        except Exception as e:
             print(f"   Error checking file status: {e}", file=sys.stderr)
             # Attempt cleanup before raising
             try: client.vector_stores.delete(vector_store.id)
             except: pass
             try: client.files.delete(file_obj.id)
             except: pass
             raise

    return vector_store.id # Return the VS ID for searching

def cleanup_vector_store(vector_store_id: str):
    """Attempts to delete a vector store. Ignores errors."""
    try:
        print(f"   Attempting cleanup of temporary vector store: {vector_store_id}")
        client.vector_stores.delete(vector_store_id)
        print(f"   Vector store {vector_store_id} deleted.")
    except Exception as e:
        print(f"   Warning: Could not delete vector store {vector_store_id}: {e}", file=sys.stderr)


def run_file_search(query: str, vector_store_id: str, model: str = DEFAULT_FILE_MODEL) -> Dict[str, Any]:
    """Perform a file_search tool call using the Responses API."""
    print(f"   Running file search in vector store {vector_store_id}...")
    response = client.responses.create(
        model=model,
        input=query, # Use 'input'
        tools=[{
            "type": "file_search",
            "vector_store_ids": [vector_store_id] # Pass VS ID here
        }],
        # Optionally include search results in the response:
        # include=["file_search_call.results"]
    )
    # The primary text output is in response.output_text
    # The structured output (including calls and citations) is in response.output
    return {
        "query": query,
        "created": _timestamp(),
        "model": model,
        "vector_store_id": vector_store_id,
        "output_text": response.output_text,
        "output_structured": [item.to_dict() for item in response.output], # Store structured output
    }


# ------------------------------ DRIVER ----------------------------------- #

def process_job(job: Dict[str, Any], outdir: pathlib.Path, sleep_s: float):
    job_type = job["type"].lower()
    # Use specific models from config or job, default to API defaults if needed
    model = job.get("model")

    if job_type == "web":
        if not model: model = DEFAULT_WEB_MODEL
        result = run_web_search(job["query"], model=model)

    elif job_type == "file":
        if not model: model = DEFAULT_FILE_MODEL
        vector_store_id = None # Ensure it's defined for finally block
        try:
            # File jobs now require a file_path for this script's workflow
            if "file_path" not in job:
                raise ValueError("Job type 'file' requires 'file_path' for this script.")

            path = pathlib.Path(job["file_path"]).expanduser()
            if not path.exists():
                raise FileNotFoundError(f"File not found at path: {path}")

            # Setup temporary vector store for this file
            vector_store_id = setup_file_search_vector_store(path)

            # Run the search using the vector store ID
            result = run_file_search(job["query"], vector_store_id=vector_store_id, model=model)

        finally:
             # Attempt to clean up the temporary vector store regardless of search success
             if vector_store_id:
                 cleanup_vector_store(vector_store_id)

    else:
        raise ValueError(f"Unknown job type: {job_type}")

    # write output
    out_path = outdir / job["out"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Use default=str for any non-serializable objects (like datetime, though we convert)
    out_path.write_text(json.dumps(result, indent=2, default=str))
    # Resolve paths before calculating relative path for printing
    try:
        relative_out_path = out_path.resolve().relative_to(pathlib.Path.cwd().resolve())
        print(f"✅ Saved → {relative_out_path}")
    except ValueError:
        # Fallback to absolute path if relative_to still fails
        print(f"✅ Saved → {out_path.resolve()}")

    # polite pause
    smart_sleep(sleep_s)


# ------------------------------------------------------------------------- #

def cli():
    # Declare global intent *before* using the variable
    global POLL_INTERVAL_S

    p = argparse.ArgumentParser(
        description="Bulk Responses API (web_search/file_search) harvester.",
        formatter_class=argparse.RawDescriptionHelpFormatter # Keep formatting
    )
    p.add_argument("jobs", help="YAML file describing jobs to run")
    p.add_argument("--outdir", default="stash", help="Directory to store raw responses")
    p.add_argument("--sleep", type=float, default=DEFAULT_SLEEP, help="Seconds to wait between *main* API calls")
    # Now it's safe to use POLL_INTERVAL_S here as it refers to the global one
    p.add_argument("--poll-interval", type=float, default=POLL_INTERVAL_S, help="Seconds between vector store status checks")

    args = p.parse_args()

    # Update global poll interval if specified (this now correctly modifies the global)
    POLL_INTERVAL_S = args.poll_interval

    if not os.getenv("OPENAI_API_KEY"):
        sys.exit("❌  OPENAI_API_KEY env var missing")

    # Create a unique output directory for this run
    run_timestamp = _timestamp()
    outdir = pathlib.Path(args.outdir) / run_timestamp
    outdir.mkdir(parents=True, exist_ok=True) # Create base output dir now

    print(f"📦 Output directory: {outdir}")

    try:
        with open(args.jobs, "r", encoding="utf-8") as fh:
            jobs: List[Dict[str, Any]] = yaml.safe_load(fh)
            if not isinstance(jobs, list):
                 raise TypeError(f"Expected YAML file to contain a list of jobs, got {type(jobs).__name__}")
    except FileNotFoundError:
        sys.exit(f"❌ Jobs file not found: {args.jobs}")
    except yaml.YAMLError as e:
        sys.exit(f"❌ Error parsing YAML file {args.jobs}: {e}")
    except TypeError as e:
         sys.exit(f"❌ Error in YAML structure: {e}")
    except Exception as e:
        sys.exit(f"❌ Error reading jobs file {args.jobs}: {e}")


    print(f"🚀 Running {len(jobs)} jobs defined in {args.jobs}\n---")

    job_failures = 0
    for i, job in enumerate(jobs, 1):
        job_id_str = f"[{i}/{len(jobs)}]"
        job_desc = job.get('query') or job.get('file_path') or job.get('out', 'Unknown')
        print(f"\n{job_id_str} {job.get('type', 'UNKNOWN').upper()} :: {job_desc}")
        try:
            process_job(job, outdir, args.sleep)
        except FileNotFoundError as e:
            print(f"⚠️ Error Job {i}: Input file not found - {e}", file=sys.stderr)
            job_failures += 1
        except ValueError as e:
            print(f"⚠️ Error Job {i}: Configuration error - {e}", file=sys.stderr)
            job_failures += 1
        except APIError as e:
            print(f"⚠️ Error Job {i}: OpenAI API Error - Status={e.status_code}, Message={e.message}", file=sys.stderr)
            job_failures += 1
        except Exception as e:
            print(f"⚠️ Error Job {i}: Unexpected error - {type(e).__name__}: {e}", file=sys.stderr)
            job_failures += 1
            # Optional: Add traceback for unexpected errors
            # import traceback
            # traceback.print_exc(file=sys.stderr)

    print("\n---")
    if job_failures == 0:
        print("🎉 All jobs completed successfully.")
    else:
        print(f"🏁 All jobs processed. {job_failures} job(s) encountered errors.")


if __name__ == "__main__":
    cli() 