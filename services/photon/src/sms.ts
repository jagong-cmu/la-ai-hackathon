/**
 * Text messages through Photon Spectrum (`spectrum-ts`).
 *
 * Confirmed by the docs (photon.codes/docs/spectrum-ts/...) and the 12.11.0 type definitions:
 * - `Spectrum({ projectId, projectSecret, providers: [imessage.config()] })`
 * - send: `imessage(app).user(phone)` -> `im.space.create(user)` -> `space.send(text)`
 * - receive: `for await (const [space, message] of app.messages)` (stream mode), or
 *   `app.webhook(request, handler)` (webhook mode; header X-Spectrum-Signature, needs webhookSecret)
 *
 * The Spectrum provider is iMessage. A recipient without iMessage may get SMS or RCS
 * (the user record has `service: "iMessage" | "SMS" | "RCS"`), but the docs do not state an
 * automatic SMS fallback. TODO(confirm): test SMS delivery to a non-iMessage team phone.
 *
 * Without Photon credentials, `DryRunMessenger` is used: it logs a redacted line and sends nothing.
 */
import type { Config } from "./config.js";
import { normalizePhone } from "./config.js";

export interface InboundText {
  id: string;
  from: string; // E.164 phone, or "" when the SDK gives no phone
  text: string;
}

export interface Messenger {
  readonly mode: "spectrum" | "dry-run";
  send(to: string, text: string): Promise<{ messageId: string }>;
  /** Start delivering inbound texts (stream mode). Returns a stop function. */
  listen(onText: (msg: InboundText) => void): Promise<() => Promise<void>>;
  /** Webhook mode: hand one inbound Spectrum webhook request to the SDK. */
  webhook?(request: Request, onText: (msg: InboundText) => void): Promise<Response>;
}

const redact = (phone: string) => (phone.length > 4 ? `***${phone.slice(-4)}` : "***");

export class DryRunMessenger implements Messenger {
  readonly mode = "dry-run" as const;
  private n = 0;
  async send(to: string, text: string) {
    this.n += 1;
    console.log(`[sms dry-run] to ${redact(to)}: ${text.length} chars (no Photon credentials; nothing sent)`);
    return { messageId: `dry_${this.n}` };
  }
  async listen() {
    return async () => {};
  }
}

// The SDK's generics are deep; this service uses a small structural view of them.
interface SpectrumLikeMessage {
  id: string;
  direction: "inbound" | "outbound";
  content: { type: string; text?: string };
  sender?: { id?: string; address?: string; phone?: string } | undefined;
}

function toInbound(message: SpectrumLikeMessage): InboundText | null {
  if (message.direction !== "inbound" || message.content.type !== "text") return null;
  // TODO(confirm): the docs show `sender?.id`; for iMessage it should be the E.164 handle.
  const raw = message.sender?.phone ?? message.sender?.address ?? message.sender?.id ?? "";
  return { id: message.id, from: normalizePhone(raw), text: message.content.text ?? "" };
}

export async function createMessenger(config: Config): Promise<Messenger> {
  const { projectId, projectSecret, webhookSecret } = config.photon;
  if (!projectId || !projectSecret) return new DryRunMessenger();

  const { Spectrum } = await import("spectrum-ts");
  const { imessage } = await import("spectrum-ts/providers/imessage");
  const app = await Spectrum({
    projectId,
    projectSecret,
    providers: [imessage.config()],
    ...(webhookSecret ? { webhookSecret } : {}),
    options: { flattenGroups: true },
  });
  const im = imessage(app);

  return {
    mode: "spectrum",
    async send(to, text) {
      const user = await im.user(to);
      const space = await im.space.create(user);
      const sent = (await space.send(text)) as { id?: string } | undefined;
      return { messageId: sent?.id ?? "" };
    },
    async listen(onText) {
      let stopped = false;
      void (async () => {
        try {
          for await (const [, message] of app.messages) {
            if (stopped) break;
            const inbound = toInbound(message as unknown as SpectrumLikeMessage);
            if (inbound) onText(inbound);
          }
        } catch (err) {
          console.error(`[sms] inbound stream stopped: ${(err as Error).message}`);
        }
      })();
      return async () => {
        stopped = true;
        await app.stop();
      };
    },
    async webhook(request, onText) {
      return app.webhook(request, async (_space, message) => {
        const inbound = toInbound(message as unknown as SpectrumLikeMessage);
        if (inbound) onText(inbound);
      });
    },
  };
}
