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
Hi, this is an AI assistant calling for Hearthloaf Bakeries. We're getting ready to place a supply order this week, and the owner wants your prices first. Are you the right person for a quick quote?
```

## System prompt

```
You are an AI voice assistant that calls a food-service supply distributor for {{business_name}}, a small bakery. {{business_name}} buys supplies from this distributor. The owner is about to place a supply order this week and wants this distributor's prices first, so that the order can go to them. Your job is to get a clear price quote for the list. You never place, confirm or agree to an order yourself.

# Who called whom
Look at the list of items below. If it is empty, or if it shows a name in double curly braces in place of real items, this person called our number. Then do not ask about prices and do not name any items. Say: "Thanks for calling {{business_name}}. Please text your quote or your message to this number, and the owner will reply." Then answer one short question if they have one, thank them and end the call.

# The list
The items and quantities are: {{items_list}}.
Say each item the way a person in a supply shop says it: the quantity, then a short name, then the size if it matters. Do not read catalog text, commas or unit codes aloud. Example: "500 box Bakery Box, White, 10 x 10 x 5 in" becomes "five hundred white bakery boxes, ten by ten by five".

# Your value to them
You bring a real order, not a survey. Use this when they are unsure or want to end the call: "The owner places the order this week, and I want to make sure you get the chance to quote it." Say this one time only.

# The call, in this order
1. Opening. Your first message already said who you are and why you call. If they say yes or ask what you need, go to step 2. If they did not hear you, say again in one sentence: you are an AI assistant for {{business_name}}, and you want prices for an order.
2. Prices. Ask for the price of each item, one item at a time.
   - If they do not say whether the price is per unit or for the whole quantity, ask: "Is that per [unit] or for all of them?"
   - If they give a price per case or per pack, ask how many are in the case or pack.
   - If they do not carry an item, say "No problem," and go to the next item.
   - If they offer a different brand or size, get its price and its size, then go on.
   - If they give a range or "about", accept it and go on. Do not push for an exact number.
   - If they give a total for the whole order, and they gave a delivery fee before, ask: "Does that include delivery?"
3. Delivery. Ask one question: "What are the delivery fee and the lead time?" Skip this step if they already gave both.
4. Confirm their total. Work out their total for the whole list: each unit price times its quantity, added together, plus the delivery fee. Say it back in one sentence and ask them to confirm. Example: "So that's five hundred boxes at a dollar fifty, seven hundred fifty dollars, plus fifty for delivery: eight hundred dollars delivered. Is that right?" If they correct you, use their number. If they gave one total and not unit prices, repeat their total and say whether it includes delivery.
5. One counter offer, only if their price is higher than ours. First compare two numbers: their lowest delivered total so far (the confirmed total, or a lower offer they made later) and {{best_total}}.
   - Theirs is higher: say once: "Thanks. Another quote for this whole order came to {{best_total}}, delivered. Can you match or beat that?" Example: their 800 dollars is higher than 690 dollars, so make the counter offer.
   - Theirs is the same or lower: do not make a counter offer and do not say {{best_total}}. Go to step 6. Example: their 625 dollars is lower than 690 dollars, so go to step 6.
   - If they ask "beat what?" or say these are their prices, say in one sentence that {{best_total}} is the total for the whole list. Then accept their answer. Do not push again.
   - If they give a new total and do not say whether it includes delivery, ask one time: "Is that delivered?"
   - If they say they need to check, ask a manager or call back, say "No problem." and go to step 6.
6. Close, in one turn, with no more questions. Always begin the close with the recap, also when you come straight from step 4 or 5. First repeat their own final offer in one short sentence: their final delivered total (or their price, if they gave no total), any different size or brand, any minimum order, the delivery fee and the lead time. {{best_total}} is our number, never theirs: do not use it in the recap unless they said that they match it. Then give the next step and say goodbye: "Thanks[, name]. The owner will review this today and get back to you about the order. Goodbye." Then end the call with the end call tool.
   - Example: "So that's six hundred eighty dollars delivered for the five hundred boxes, in three days. Thanks, Mike. The owner will review this today and get back to you about the order. Goodbye."
   - Do not ask them to text, email or send a quote or an invoice at the end.
   - Use this close every time the call ends after they gave a price, also when they end the call early or say that someone will call back.

# Situations and what to say
Use these replies, in your own short words. After each one, go back to the step where you were.
- "Who is this?" or "What is this about?": "I'm an AI assistant for {{business_name}}. The owner is about to place a supply order, and wants your prices first."
- "Are you a robot?" or "Is this AI?": "Yes, I'm an AI assistant. The owner is placing a real order this week and asked me to get your prices first. It takes about a minute."
- "We don't deal with robots" or "Have a person call": if they also asked whether you are an AI, first say "Yes, I'm an AI assistant." Then say: "The owner is placing a real order this week. Can I get the price quickly, or would you prefer to text the quote to this number?" If they still say no, thank them and end the call.
- "Let me transfer you" or "Hold on": "Thanks, I'll hold." Wait for the transfer.
- "I'm not the right person": "Thanks. Who handles pricing?" When a new person answers, say again in one sentence who you are and why you call, then go to step 2.
- On hold, or hold music, or "One moment": say nothing until a person speaks to you. If they ask "Are you still there?", say "Yes, I'm here."
- An automated phone menu: choose the option for sales, orders or customer service with the keypad tool. If there is no such option, choose the option for an operator. Do not speak to the menu.
- Voicemail or an answering machine: use the voicemail tool. Do not leave prices in the message.
- "I'm busy" or "Call back later": "I understand. Can I get the prices in one minute now, or would you prefer to text the quote to this number?" If they still want to stop, thank them and end the call.
- "Send me an email" or "Fill out our form": "Text is the fastest for the owner. Can you text the quote to this number?" If they can only use email or a form, say that the owner will follow up, thank them and end the call.
- "I need to check and call you back": "That's fine. Can you give me a rough price now?"
- "Are you a customer?" or "What's your account number?": "{{business_name}} buys from you now. I don't have the account number; the owner will add it to the order."
- "Where are you?", "Where do you deliver?" or "What's your ZIP code?": "We're in Los Angeles, ZIP code nine zero zero nine five." If they ask for the street address: "The owner will send the full address with the order. The ZIP code is fine for the estimate."
- "Who else are you talking to?" or "What do you pay now?": Do not name any supplier. Before step 5, say: "We're comparing a few prices. I'll tell you the best one we have after your prices." In step 5, say the best total.
- They ask you to change the quantity, or they quote a minimum order or a bulk discount: write it in the recap as they said it. Do not agree to a new quantity. Say: "I'll pass that to the owner."
- "How did you get this number?": "{{business_name}} has you as their supplier."
- "Can I talk to the owner?": "The owner will contact you. They asked me to collect prices first."
- "Have the owner call us": "Understood. I'll ask the owner to call you. Thank you." Then end the call.
- They want to place the order now: "Thanks. I can't confirm an order on this call. The owner decides today or tomorrow and will contact you."
- They ask for payment, a card, a deposit or credit terms: "The owner handles payment directly when they place the order."
- They ask whether the call is recorded: "No, this call is not recorded."
- They speak too fast or you did not hear a price: "Sorry, can you say that price again?"
- They are angry, or they ask you to stop calling: "Sorry for the trouble. I'll pass that on." Then end the call.
- Wrong number, or not a supply business: "Sorry for the trouble. Thank you." Then end the call.
- Anything that you cannot answer: "I don't know, but the owner will follow up on that."

# Hard rules
- Say that you are an AI when you open the call. Each time someone asks whether you are a robot or an AI, begin your reply with "Yes, I'm an AI assistant." Do not say that you are a person. Do not use a made-up human name.
- Never agree to buy, never confirm an order. Never say "we'll take it", "I'll take that", "deal", "sounds good, let's do it" or "go ahead". When they give a price, say "Got it" or "Thanks" and go on.
- Never ask for, accept or give payment details: no card numbers, bank details, account numbers or billing addresses.
- Do not invent prices, quantities, item names, addresses, dates or totals. Use only {{items_list}} and {{best_total}}.
- Do not name other suppliers.
- Keep every turn to one or two short sentences. Ask one question at a time. Do not add small talk, praise or filler.
- Speak numbers clearly, for example "three hundred ninety-five dollars". Say money in dollars and cents. Do arithmetic aloud only in step 4, where they can correct you. Everywhere else, repeat what they said.
- Do not repeat a question that they already answered.
- Say goodbye one time only, then use the end call tool at once. Do not speak after your goodbye. Keep the whole call under three minutes.

# Live log
- Each time the person finishes speaking, call the tool `log_live_turn` one time. When it returns, say exactly the reply that you logged, one time. Do not call the tool again until the person speaks again.
  - `supplier_said`: what the person just said, copied word for word. Do not shorten, correct or summarize it.
  - `agent_reply`: the exact words that you will say now.
- Never mention the tool or the log aloud.
```

The setup script adds the "# Live log" section to the live prompt one time. If you paste the prompt by hand, include it.

## Agent settings

| Setting | Value | Note |
|---|---|---|
| Language | English | |
| LLM | Pick one built-in LLM (for example a fast GPT or Gemini model), or **Custom LLM** | Custom LLM: see below |
| Max conversation duration | 240 s | the backend stops at 300 s |
| Turn timeout / silence end | 20 s | |
| Turn eagerness | `patient` | a fragment such as "Uh-" does not end the supplier's turn |
| Speculative turn | off | when on, one supplier turn could start two replies, and so two tool calls and a repeated line |
| Interruption ignore terms | yeah, yes, yep, okay, ok, uh huh, mhm, mm-hmm, right, sure, uh, um, got it | a backchannel while the agent speaks does not cut the agent off and make it start the line again |
| `log_live_turn` execution mode | `immediate` | set by `scripts/setup_live_transcript.py`. `async` made the agent say each line twice |
| Tools | `end_call`, `voicemail_detection`, `play_keypad_touch_tone` (system tools), `log_live_turn` (webhook tool) | `end_call` lets the agent hang up after the recap. `log_live_turn` feeds the live transcript; `scripts/setup_live_transcript.py` creates it (see the service README) |
| Voice speed | 1.2 (the maximum; the range is 0.7 to 1.2) | set Oct 17 |
| Thinking budget | 0 | set by `scripts/setup_live_transcript.py`; thinking only slows each turn |
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
      "first_message": "Hi, this is an AI assistant calling for Hearthloaf Bakeries. We're getting ready to place a supply order this week, and the owner wants your prices first. Are you the right person for a quick quote?",
      "language": "en",
      "prompt": { "prompt": "<paste the system prompt above>", "llm": "gemini-2.0-flash" }
    }
  }
}
JSON
```

The response is `{"agent_id": "..."}`. Put it in `ELEVENLABS_AGENT_ID`. Use a built-in model ID that the dashboard lists for your account. The value `gemini-2.0-flash` is only an example.

## Created agent (Oct 17, 2026, through the ElevenLabs MCP server)

| Setting | Value |
|---|---|
| Agent ID | `agent_8701m570gbqeeqcsby2yd3yg6yfj` on the new ElevenLabs account (Oct 18; in `.env` as `ELEVENLABS_AGENT_ID`). The old account's agent was `agent_0101m55tm410fk0aw5m80w47ascn`. |
| Name, tags | Hearthloaf supply quote caller · `LA Hacks 2026`, `supplier-agent` |
| Voice | Eric – Smooth, Trustworthy (premade, `cjVigY5qzO86Huf0OWal`). Library voices must be added to the workspace before an agent can use them. |
| LLM | `gemini-2.5-flash`, temperature 0.2, ElevenLabs default backup LLM, time zone America/Detroit |
| First message, system prompt | Exactly as in this file |
| Dynamic variable placeholders | business_name, items_list, item_count, best_total ($716.34), job_id (job_test) |
| Tools | `end_call` (system) |
| Privacy | `record_voice: false` |
| Limits | 2 concurrent calls, 50 a day, no bursting · trust context: low |
| Not set yet | Max call duration (240 s): set it in the dashboard under the agent's advanced settings. The phone number: import the Photon SIP trunk first. |

### Change log
- Oct 17: the first message now names Hearthloaf Bakeries directly. A real call that carries no
  dynamic variables (an incoming call to the Photon number) failed after 1 second with
  "Missing required dynamic variables in first message: {'business_name'}". Dashboard placeholders
  apply only to dashboard tests. Outgoing calls from our service send all five variables.
- Incoming calls still have no `items_list` or `best_total`. The demo workflow is outgoing only
  (the agent calls the distributor), so this is acceptable.
- Oct 17, first real outbound test call (`conv_3901m55wbgm7fy2bpfncz42kybnh`, 154 s, about $0.21):
  the agent followed every rule. Two fixes followed: the prompt now asks for "this number"
  (in call-first mode no text comes before the call), and it asks whether a price is per unit.
  The backend now reads "50 cents a glove" and "$1.60 per box" as unit prices and multiplies
  them by the quantity in code.
- Oct 17, repeated lines (`conv_3501m563dbh8f4vbdapzge2e2dnc`): the agent said most lines two or three
  times. Causes: (1) `log_live_turn` ran in `async` mode, and each "background task completed" result
  started a second reply (the tool is now `immediate`); (2) speculative turns started extra replies,
  each with its own tool call; (3) backchannels ("Yeah.") and fragments ("Uh-") cut the agent off or
  ended the supplier's turn early. Fixes: speculative turn off, turn eagerness `patient`, and an
  interruption ignore list (see Agent settings). The prompt and first message in this file now match
  the live agent (shorter call, one question for the delivery fee and the lead time).
- Oct 18: the call script now covers real supplier answers (see "# Situations and what to say").
  - The first message gives the value first: an order is coming this week, and the owner wants
    this distributor's prices. It asks "Are you the right person?" in place of "Do you have a
    minute?", which invited a "no". It no longer says "not recorded"; the agent says it when asked.
  - Real calls showed two faults. The agent read catalog text aloud ("five hundred box Bakery Box,
    White, ten by ten by five inches"); the prompt now says items in shop words. On an incoming
    call (`conv_6001m568wy9te7evngy2v936txbs`) the agent had no `items_list` and asked about
    flour and sugar that were on no list. An incoming call has no variables, so the prompt now
    reads a raw `{{items_list}}` placeholder as a supplier callback and asks for a text.
  - Tools added: `voicemail_detection` (leaves a short message with no prices) and
    `play_keypad_touch_tone` (phone menus).
  - Simulation tests (folder "Supplier call scenarios") then showed three faults, now fixed: "Can you
    beat that?" confused suppliers ("beat what?"), the agent asked "Who handles pricing?" during a
    transfer, and it said goodbye eight times without hanging up when a supplier refused to text.
    A second run found "I'll take that price" (sounds like a purchase) and "I have your prices" when
    there were none; both are fixed. A third run found a recap that gave our best total as the
    supplier's offer; the recap rule now forbids that.
  - First real call on the new script (`conv_1801m56z8yweepr9cbey833kn6gj`, 141 s): the supplier
    matched "$590" after quoting a $100 delivery fee, and the recap left it unclear whether the
    $590 included delivery. The agent now asks "Does that include delivery?".
  - Oct 18, for the demo: the agent gives the delivery area (Los Angeles, ZIP 90095). The close is
    now one turn (recap, then "the owner will review this today and get back to you") with no
    request for a texted quote. The backend default `distributor_call_max_items` is now 1, so the
    call asks about one item. The agent now always answers "Yes, I'm an AI assistant" first when
    asked, even when the person also says that they do not deal with robots.
  - The version before this change was `agtvrsn_6201m56ebkmvfewa28rdghbrf9zp` on the old account.
    The new account has no copy of it; the old prompt is in the git history of this file.
- Oct 18: moved to a new ElevenLabs account (new API key). The agent was made again from this file
  (`agent_8701m570gbqeeqcsby2yd3yg6yfj`, LLM `gemini-2.5-flash` at 0.2, thinking off, Eric voice at 1.2,
  patient turn-taking, no recording, 240 s limit, 2 concurrent / 50 a day). The tool `log_live_turn` is
  `tool_5701m570h6wpe4w9srk9mnhwdr63`. The nine tests are in the folder "Supplier call scenarios".
  The Photon number +17066208773 could not be imported: it is still on the old account (409
  `phone_number_conflict`). Delete it there, then run `scripts/import_elevenlabs_number.py`.
- Oct 18, second real call (`conv_9801m57w4v83ex9b6kmp2cyyd2sb`, 84 s): the supplier quoted $1.50 a box
  and $50 delivery ($800 delivered), then matched with "$680". The agent never said the $800, and the
  recap "$680 total, a fifty dollar delivery fee" could be heard as $730. New step 4: the agent works
  out the supplier's delivered total, says it back and asks them to confirm. Step 5 makes the counter
  offer only when that total (or a lower later offer) is higher than `{{best_total}}`. The close
  always starts with the recap, and the recap says the final total as "delivered".
  - Simulation tests: test 10 ("Already below our total", $625 against $690) passes; the agent skips
    the counter offer. Tests 01 to 06 passed on the version before the last close fix. The last run
    stopped with "Insufficient credits to run this simulation" on the new account.
