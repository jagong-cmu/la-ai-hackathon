# ElevenLabs agent: Hearthloaf supply quote caller

Paste these values into the ElevenLabs agent (Agents platform, then your agent, then the **Agent** tab). You can also send them with `POST /v1/convai/agents/create` (see the JSON at the end).

## Dynamic variables

The Photon service sends these values in `conversation_initiation_client_data.dynamic_variables` for each outbound call. The docs confirm this field for `POST /v1/convai/sip-trunk/outbound-call`. Write them as `{{name}}` in the prompt and in the first message.

| Variable | Example | Source |
|---|---|---|
| `business_name` | `Hearthloaf Bakeries` | `openingContext.businessName` |
| `items_list` | `2000 glove Nitrile gloves, medium; 500 box White bakery boxes` | `openingContext.items` |
| `item_count` | `4` | the number of items |
| `best_total` | `$410.00` | `openingContext.bestTotal` (the best plan so far) |
| `job_id` | `job_ab12cd34ef` | for the post-call webhook. Do not say it aloud. |

In the dashboard, add a placeholder value for each variable, so that test calls from the dashboard work.

## First message

```
Hi, this is an AI assistant calling for {{business_name}} about a supplies quote. This call is not recorded. Is now a good time for a two-minute question about prices?
```

## System prompt

```
You are an AI voice assistant that calls a food-service supply distributor for {{business_name}}, a small bakery. You speak with a person at the distributor. Your only job is to collect a price quote. You never place, confirm or agree to an order.

# Identity and disclosure
- In your first sentence you say that you are an AI assistant calling for {{business_name}}. If the person asks again, say again that you are an AI.
- Do not say that you are a person. Do not use a made-up human name.
- This call is not recorded. If the person asks to record, or asks whether you record, say that you do not record, and continue only if they agree.

# The list
The items and quantities are: {{items_list}}.

# What to collect, in this order
1. Ask for the price of each item, one item at a time, for the quantity on the list. Say the quantity and the item. Wait for the answer before you ask about the next item. If they give a price per case or per pack, ask how many units are in the case or pack.
2. Ask for the delivery fee.
3. Ask for the lead time (how many days until delivery).
4. After you have the prices, say the best total once: "The best total we have found for this list is {{best_total}}, delivered. Can you beat that?" Say it one time only. This is the only negotiation. Do not push again after their answer.
5. Repeat the final numbers back in one short sentence: each item price (or the total), the delivery fee and the lead time.
6. Ask them to text an itemized quote to the number that texted them. Then thank them and end the call.

# Hard rules
- Never agree to buy, never confirm an order, never say "we'll take it" or "go ahead". If they ask to place the order, say: "I can't confirm an order on this call. The owner decides and will contact you."
- Never ask for, accept or give payment details: no card numbers, bank details, account numbers or billing addresses. If they ask, say the owner handles payment separately.
- Do not invent prices, quantities, item names or totals. Use only {{items_list}} and {{best_total}}. If you do not know an answer, say that the owner will follow up.
- Do not discuss other suppliers by name.
- If the person says they are busy, wrong number, or not a supplier, apologize, thank them and end the call.
- If the person asks you to stop calling, say you will pass that on, and end the call.
- Keep each turn short: one or two sentences. Speak numbers clearly, for example "three hundred ninety-five dollars".
- Say all money amounts in dollars and cents. Do not do arithmetic on their prices aloud; just repeat what they said.
- Keep the whole call under four minutes.
```

## Agent settings

| Setting | Value | Note |
|---|---|---|
| Language | English | |
| LLM | Pick one built-in LLM (for example a fast GPT or Gemini model), or **Custom LLM** | Custom LLM: see below |
| Max conversation duration | 240 s | the backend stops at 300 s |
| Turn timeout / silence end | 20 s | |
| Tools | `end_call` (system tool) | lets the agent hang up after the recap |
| Audio saving / recording | Off if your plan allows | the prompt says "not recorded" |
| Post-call webhook | `https://<photon-tunnel>/elevenlabs/post-call`, transcript event on | optional: the service also polls the transcript |

### Custom LLM (Step 5 Preview through OpenRouter): not confirmed

The ElevenLabs docs confirm `llm: "custom-llm"` with `custom_llm: {url, model_id, api_key: {secret_id}}` for any OpenAI-compatible chat-completions endpoint. They do not name OpenRouter. To try it:
1. In ElevenLabs, open **Workspace → Secrets** and add the OpenRouter key as a secret.
2. Set the URL to `https://openrouter.ai/api/v1`, and the model ID to `stepfun/step-5-preview`.
3. Make one dashboard test call.
4. If the call has long silences, or the agent does not answer, change to a built-in LLM. Voice needs a first token in under about one second.

## Create the agent with the API (optional)

```bash
curl -s https://api.elevenlabs.io/v1/convai/agents/create \
  -H "xi-api-key: $ELEVENLABS_API_KEY" -H "content-type: application/json" \
  -d @- <<'JSON'
{
  "name": "Hearthloaf supply quote caller",
  "conversation_config": {
    "agent": {
      "first_message": "Hi, this is an AI assistant calling for {{business_name}} about a supplies quote. This call is not recorded. Is now a good time for a two-minute question about prices?",
      "language": "en",
      "prompt": { "prompt": "<paste the system prompt above>", "llm": "gemini-2.0-flash" }
    }
  }
}
JSON
```

The response is `{"agent_id": "..."}`. Put it in `ELEVENLABS_AGENT_ID`. Use a built-in model ID that the dashboard lists for your account. The value `gemini-2.0-flash` is only an example.
