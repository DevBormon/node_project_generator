import os, json, sys
from dotenv import load_dotenv

load_dotenv()  # reads .env file


def count_input_words(messages):
    text = ""

    for m in messages:
        # handle standard chat format
        if isinstance(m.get("content"), str):
            text += " " + m["content"]

        # if tool messages or multi-part content appears
        elif isinstance(m.get("content"), list):
            for part in m["content"]:
                if isinstance(part, dict) and "text" in part:
                    text += " " + part["text"]

    return len(text.split())

def count_output_words(response):
    if os.environ.get("GROQ_API_KEY") or os.environ.get("OPENAI_API_KEY") or os.environ.get("HF_TOKEN"):
        text = response.choices[0].message.content or ""
    elif os.environ.get("GEMINI_API_KEY"):
        text = response.text
    return len(text.split())
    
def call_llm(system_prompt , user_prompt, temperature= 0.3):
    msg = [{"role": "system", "content": system_prompt},{"role": "user", "content": user_prompt}]
    
    # print("=" * 70)
    # print(msg)
    # print("=" * 70)
        
    # input_wc = count_input_words(msg)

    if os.environ.get("GROQ_API_KEY"):
        from groq import Groq

        client = Groq(api_key=os.environ["GROQ_API_KEY"])
       
        model = os.environ["MODEL_NAME"]
            
        r = client.chat.completions.create(
            model=model,
            messages=msg,
            temperature=temperature,
            # max_completion_tokens=1000,
        )
        
        # output_wc = count_output_words(r)
        
        # print("=" * 70)
        # print("Input words:", input_wc)
        # print("Output words:", output_wc)
        # print("Total words:", input_wc + output_wc)
        # print("=" * 70)

        if __name__ == "__main__":
            print(client.models.list())

        return r.choices[0].message.content
    elif os.environ.get("GEMINI_API_KEY"):
        from google import genai
        from google.genai import types
        
        system_text = msg[0]["content"] if msg[0]["role"] == "system" else None
        user_text = msg[-1]["content"]  # last message is user        
        
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        r = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=user_text,
            config=types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=1000,
                system_instruction=system_text
            )
        )
        
        # output_wc = count_output_words(r)
        
        # print("=" * 70)
        # print("Input words:", input_wc)
        # print("Output words:", output_wc)
        # print("Total words:", input_wc + output_wc)
        # print("=" * 70)
        
        return r.text
    elif os.environ.get("HF_TOKEN"):
        from openai import OpenAI
        
        client = OpenAI(
            api_key=os.environ["HF_TOKEN"],
            base_url="https://router.huggingface.co/v1"
        )

        r = client.chat.completions.create(
            # model="moonshotai/Kimi-K2-Instruct-0905",
            model="deepseek-ai/DeepSeek-V4-Pro:novita",
            messages=msg,
        )
        
        # output_wc = count_output_words(r)
        
        # print("=" * 70)
        # print("Input words:", input_wc)
        # print("Output words:", output_wc)
        # print("Total words:", input_wc + output_wc)
        # print("=" * 70)

        if __name__ == "__main__":
            print("Show Models:")
            for model in client.models.list():
                print(model.id)

        return r.choices[0].message.content
    elif os.environ.get("OPENROUTER_API_KEY"):
        from openai import OpenAI, APIError, AuthenticationError, RateLimitError
        
        model = os.environ["OPENROUTER_MODEL"]
        
        err = []

        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
        )

        
        try:
            r = client.chat.completions.create(
                model=model,
                messages=msg,
                temperature=temperature,
            )

            if not r.choices:
                err.append(json.dumps({"error": "Empty response from LLM"}))
            else:
                return r.choices[0].message.content

        except json.JSONDecodeError:
            err.append(json.dumps({"error": "OpenRouter returned non-JSON (rate limit, maintenance, or network issue). Check https://status.openrouter.ai/"}))
        except AuthenticationError as e:
            err.append(json.dumps({"error": f"Bad API key: {e}"}))
        except RateLimitError as e:
            err.append(json.dumps({"error": f"Rate limited: {e}"}))
        except APIError as e:
            err.append(json.dumps({"error": f"API error: {e}"}))
            
        if err:
            for e in err:
                print(e)
        

    raise ValueError(
        "No provider configured. Set GROQ_API_KEY, GEMINI_API_KEY, HF_TOKEN, or OPENROUTER_API_KEY."
    )


if __name__ == "__main__":
    print(call_llm("","Say hello in one word"))