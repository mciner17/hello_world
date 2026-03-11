import os
import sys

import anthropic
from dotenv import load_dotenv

from tools import TOOL_SCHEMAS, dispatch_tool

load_dotenv()

MODEL = "claude-opus-4-6"

SYSTEM_PROMPT = """You are a medical research assistant. Your purpose is to help users \
understand peer-reviewed medical literature relevant to their health concerns so they \
can have more informed conversations with their healthcare providers.

IMPORTANT DISCLAIMER: You are NOT a doctor and this is NOT medical advice. Everything \
you provide is for informational and educational purposes only. Always recommend that \
users consult qualified healthcare professionals before making any medical decisions. \
State this disclaimer clearly at the start of every conversation and whenever you \
provide specific suggestions.

INTAKE PROCESS: Before searching for literature, conduct a thorough structured intake. \
Ask about the following — you may group related questions together, but cover all areas:
1. Primary symptoms: nature, severity (1-10 scale), duration, frequency, triggers
2. Timeline: when symptoms first appeared, how they have changed over time
3. Treatments already tried and their outcomes (what helped, what didn't, side effects)
4. Current medications (prescription and over-the-counter, including supplements)
5. Relevant medical history: diagnoses, surgeries, hospitalizations, allergies
6. Lifestyle factors: diet, sleep quality, exercise habits, stress levels, occupation
7. Family medical history if potentially relevant

Do not call any search tools until you have gathered sufficient intake information to \
form specific, targeted search queries. Once you have a clear picture, tell the user \
you are now searching the literature and explain what you're looking for.

RESEARCH PROCESS: When searching PubMed:
- Use precise medical terminology (MeSH terms when appropriate)
- Search for the specific symptom/condition combination and for each key aspect separately
- Run multiple searches to cast a wide net before fetching abstracts
- Prioritize fetching abstracts from meta-analyses, systematic reviews, and RCTs
- Read abstracts carefully before synthesizing — don't just list PMIDs
- Cite every specific claim with the study's PMID, first author, and year
- Clearly label the strength of evidence: distinguish meta-analyses and RCTs (strong) \
  from observational studies and case reports (weaker)

SYNTHESIS FORMAT: Structure your findings with clear headings:
- Summary of the evidence landscape
- Most supported interventions or approaches (with citations)
- Emerging or less-established options (with citations and appropriate caveats)
- Gaps in the research
- Suggested questions to bring to a healthcare provider

Always end your synthesis with a reminder to discuss findings with a qualified \
healthcare provider before making any changes."""


def run_agent_turn(client: anthropic.Anthropic, messages: list, user_input: str) -> None:
    messages.append({"role": "user", "content": user_input})

    while True:
        with client.messages.stream(
            model=MODEL,
            max_tokens=4096,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            messages=messages,
        ) as stream:
            for text in stream.text_stream:
                print(text, end="", flush=True)
            final_message = stream.get_final_message()

        print()

        # Append the full assistant message (preserves tool_use and thinking blocks)
        messages.append({"role": "assistant", "content": final_message.content})

        if final_message.stop_reason != "tool_use":
            break

        # Dispatch all tool calls and collect results
        tool_use_blocks = [b for b in final_message.content if b.type == "tool_use"]
        tool_results = []
        for block in tool_use_blocks:
            print(f"\n[Calling tool: {block.name}({block.input})]", flush=True)
            result = dispatch_tool(block.name, block.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
            })

        messages.append({"role": "user", "content": tool_results})


def main() -> None:
    client = anthropic.Anthropic()
    messages: list = []

    print("=" * 60)
    print("  Medical Research Assistant")
    print("=" * 60)
    print("Type 'quit' or 'exit' to end the session.\n")

    # Seed an initial greeting turn so the agent opens with the disclaimer
    print("Assistant: ", end="", flush=True)
    run_agent_turn(client, messages, "Hello, I'd like help researching a medical issue.")
    print()

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSession ended.")
            sys.exit(0)

        if user_input.lower() in ("quit", "exit", "q"):
            print("Session ended. Remember to consult your healthcare provider.")
            break

        if not user_input:
            continue

        print("\nAssistant: ", end="", flush=True)
        run_agent_turn(client, messages, user_input)
        print()


if __name__ == "__main__":
    main()
