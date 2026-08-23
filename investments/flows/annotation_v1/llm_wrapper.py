import os
from openai.version import VERSION as OPENAI_VERSION
from pathlib import Path
import json
from dotenv import load_dotenv


import sys
sys.path.insert(0, "/home/matias/Documents/dev-testbed/promptflow/src/promptflow-core")

from promptflow._core.tool import tool
# from promptflow.core import tool


load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env")  # Goes up 2 levels to project root

# The inputs section will change based on the arguments of the tool function, after you save the code
# Adding type to arguments and return value will help the system show the types properly
# Please update the function name/signature per need

import json
from pathlib import Path

LOG_PATH = Path("/tmp/pf_debug_raw_.log")
_log_initialized = False

def debug_log(value, label=None):
    global _log_initialized

    try:
        # Overwrite the file only once per run
        mode = "w" if not _log_initialized else "a"
        with open(LOG_PATH, mode) as f:
            if label:
                f.write(f"\n=== {label} ===\n")
            if isinstance(value, (dict, list)):
                f.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
            else:
                f.write(str(value) + "\n")
        _log_initialized = True
    except Exception as e:
        print("Logging failed:", e)


def to_bool(value) -> bool:
    return str(value).lower() == "true"


def get_client():
    if OPENAI_VERSION.startswith("0."):
        raise Exception(
            "Please upgrade your OpenAI package to version >= 1.0.0 or using the command: pip install --upgrade openai."
        )
    api_key = os.environ["OPENAI_API_KEY"]
    conn = dict(
        api_key=os.environ["OPENAI_API_KEY"],
    )
    if api_key.startswith("sk-"):
        from openai import OpenAI as Client
    else:
        from openai import AzureOpenAI as Client
        conn.update(
            azure_endpoint=os.environ.get("AZURE_OPENAI_API_BASE", "azure"),
            api_version=os.environ.get("OPENAI_API_VERSION", "2023-07-01-preview"),
        )

    if "OPENAI_API_KEY" not in os.environ or "AZURE_OPENAI_API_BASE" not in os.environ:
        # load environment variables from .env file
        load_dotenv()

    if "OPENAI_API_KEY" not in os.environ:
        raise Exception("Please specify environment variables: OPENAI_API_KEY")

    return Client(**conn)



def load_schema(file_path: str):
    # Load JSON schema from the specified file path
    with open(file_path, 'r') as schema_file:
        return json.load(schema_file)


# Load schema once (relative to this file’s location)
# SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schemas/generate_fixes.json")
# SCHEMA_PATH = "./parsed_message_advanced.json"
# function_schema = load_schema(SCHEMA_PATH)
# function_name = 'parsed_message'



from pathlib import Path

@tool
def run_llm_schema_tool(
    prompt: str,
    # for AOAI, deployment name is customized by user, not model name.
    deployment_name: str,
    schema_path: str,
    function_name: str = "parsed_message",
    suffix: str = None,
    max_tokens: int = 16000,
    temperature: float = .4,
    top_p: float = 1.0,
    n: int = 1,
    echo: bool = False,
    stop: list = None,
    presence_penalty: float = 0,
    frequency_penalty: float = 0,
    logit_bias: dict = {},
    user: str = "",
    **kwargs,
) -> dict:





    # -------------------------------
    # 💬 Step 1: Log input details
    # -------------------------------
    debug_log(function_name, "FUNCTION NAME")
    debug_log(prompt, "PROMPT")



    # TODO: remove below type conversion after client can pass json rather than string.
    echo = to_bool(echo)


    from pathlib import Path

    schema_path = Path(schema_path).expanduser().resolve()
    assert schema_path.exists(), f"Schema path does not exist: {schema_path}"
    schema = load_schema(str(schema_path))
    # schema = load_schema(schema_path)

    if "name" not in schema or schema["name"] != function_name:
        raise ValueError(f"Schema does not match expected name '{function_name}': got {schema}")


    client = get_client()

    debug_log(function_name)
    # debug_log([schema.get("name")])
    debug_log(prompt)
    # debug_log(messages)




    # -------------------------------
    # 🚀 Step 2: Call model
    # -------------------------------

    print("\n=== 🛠 Calling LLM with the following parameters ===")
    print("Model:", deployment_name)
    print("Prompt length (chars):", len(prompt))
    print("Prompt (start):", repr(prompt[:300]))
    print("Functions:", json.dumps(schema, indent=2)[:500], "...")

    # Before model call
    debug_log(prompt, "📝 Prompt")
    debug_log(schema, "📐 Schema")
    debug_log(function_name, "🔧 Function to Call")

    try:
        response = client.chat.completions.create(

            tools=[{"type": "function", "function": schema}],
            tool_choice={"type": "function", "function": {"name": "parsed_message"}},


            messages=[
                {"role": "system", "content": "You are an expert summarization assistant. Always call the function `parsed_message` with complete structured JSON."},
                {"role": "user", "content": prompt}
            ],
            # functions=[schema],  # deprecated, but still supported
            # function_call={"name": "parsed_message"},
            model=deployment_name,
            max_tokens=int(max_tokens),
            temperature=float(temperature),
            top_p=float(top_p),
            n=int(n),
            stop=stop if stop else None,
            presence_penalty=float(presence_penalty),
            frequency_penalty=float(frequency_penalty),
            logit_bias=logit_bias or {},
            user=user,
        )

        debug_llm_response(response)

        # After model call
        debug_log(response.dict(), "📦 Full Response")
        debug_log(response.usage, "📊 Usage Info")


        # Fallback: check if the message content has anything
        if not response.choices[0].message.function_call.arguments.strip():
            debug_log(response.choices[0].message.content, "📃 Fallback content (not function_call?)")

    except Exception as e:
        print("\n=== ❗️LLM Call Failed ===")
        print("Type:", type(e).__name__)
        print("Message:", str(e))
        if hasattr(e, 'response'):
            print("Full HTTP response (OpenAI):")
            print(e.response)


    # # -------------------------------
    # # 🚀 Step 2: Call model
    # # -------------------------------
    # response = client.chat.completions.create(
    #     messages=[
    #         {"role": "system", "content": ""},
    #         {"role": "user", "content": prompt}
    #     ],
    #     functions=[schema],
    #     function_call={"name": "parsed_message"},
    #     model=deployment_name,
    #     max_tokens=int(max_tokens),
    #     temperature=float(temperature),
    #     top_p=float(top_p),
    #     n=int(n),
    #     stop=stop if stop else None,
    #     presence_penalty=float(presence_penalty),
    #     frequency_penalty=float(frequency_penalty),
    #     logit_bias=logit_bias or {},
    #     user=user,
    # )







    # # debug_log(response.choices[0].message.function_call.arguments)

    # raw_args = response.choices[0].message.function_call.arguments

    # debug_log(raw_args)

    # try:
    #     parsed = json.loads(raw_args)
    # except json.JSONDecodeError as e:
    #     raise ValueError(f"Function call output is not valid JSON:\n{raw_args}") from e

    # -------------------------------
    # 📦 Step 3: Inspect raw response
    # -------------------------------
    # choice = response.choices[0]
    msg = response.choices[0].message

    if msg.function_call:  # legacy / older models
        raw_args = msg.function_call.arguments
        fn_name = msg.function_call.name
    elif msg.tool_calls:  # new tool call format
        tool_call = msg.tool_calls[0]
        raw_args = tool_call.function.arguments
        fn_name = tool_call.function.name
    else:
        print("❌ No function or tool call found.")
        raw_args = "{}"
        fn_name = "unknown"


    # debug_log(msg.function_call.name, "✅ FUNCTION CALL NAME")
    # debug_log(msg.function_call.arguments, "📝 RAW ARGUMENTS")

    # -------------------------------
    # 🧪 Step 4: Parse function output
    # -------------------------------
    # raw_args = msg.function_call.arguments

    try:
        parsed = json.loads(raw_args)
    except json.JSONDecodeError as e:
        debug_log(raw_args, "❌ JSON DECODE ERROR — RAW")
        debug_log(str(e), "❌ JSON DECODE ERROR — EXCEPTION")
        raise ValueError(f"Function call output is not valid JSON:\n{raw_args}") from e

    # -------------------------------
    # ✅ Final: Parsed result
    # -------------------------------
    debug_log(parsed, "✅ PARSED OUTPUT")
    return parsed





import inspect
from pprint import pprint

def debug_llm_response(response):
    print("\n=== 📡 RAW RESPONSE ===")
    pprint(response)

    debug_log(response.dict(), "FULL RAW RESPONSE")

    try:
        choice = response.choices[0]
        print("\n=== ✅ CHOICE TYPE ===", type(choice))
        print("=== 📋 CHOICE OBJECT ===")
        pprint(choice)

        msg = choice.message
        print("\n=== 🧾 MESSAGE ===")
        pprint(msg)

        if msg.content:
            print("\n=== 📝 TEXT CONTENT ===")
            print(msg.content)

        if msg.function_call:
            print("\n=== ⚙️ FUNCTION CALL ===")
            print("Function Name:", msg.function_call.name)
            print("Arguments Raw:", msg.function_call.arguments)
            try:
                parsed = json.loads(msg.function_call.arguments)
                print("\n=== ✅ PARSED JSON OUTPUT ===")
                pprint(parsed)
            except Exception as e:
                print("\n=== ❌ PARSE ERROR ===")
                print("Message:", str(e))
                print("Raw Args:", msg.function_call.arguments)
        else:
            print("\n=== ⚠️ No function_call in message ===")

    except Exception as e:
        print("\n=== ❌ ERROR: Invalid response structure ===")
        print(str(e))




# def main(prompt: str, deployment_name: str, schema_path: str) -> dict:
#     # Assume we call OpenAI or similar
#     response_text = call_model(prompt, deployment_name)

#     # Load schema dynamically
#     with open(schema_path, "r") as f:
#         schema = json.load(f)

#     parsed = fun?? (response_text, schema)
#     return {"result": parsed}
