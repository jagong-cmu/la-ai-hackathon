import { serve } from "@hono/node-server";
import { makeForwarder } from "./backend.js";
import { describeConfig, loadEnvFiles, readConfig } from "./config.js";
import { createApp } from "./server.js";
import { createMessenger } from "./sms.js";
import { createVoice } from "./voice.js";

loadEnvFiles();
const config = readConfig();
console.log("[photon-service] config", describeConfig(config));

const messenger = await createMessenger(config).catch((err: Error) => {
  console.error(`[photon-service] Photon init failed (${err.message}); texts run in dry-run mode`);
  return import("./sms.js").then((m) => new m.DryRunMessenger());
});
const voice = createVoice(config);
const { app, onInboundText } = createApp({ config, messenger, voice, forward: makeForwarder(config) });

// stream and poll need no public URL. webhook needs the tunnel and POST /photon/webhook.
const pulls = config.photon.inbound === "stream" || config.photon.inbound === "poll";
const stopInbound = pulls ? await messenger.listen(onInboundText) : async () => {};

const server = serve({ fetch: app.fetch, port: config.port }, (info) => {
  console.log(`[photon-service] listening on :${info.port} (sms=${messenger.mode}, voice=${voice.mode})`);
});

for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.on(signal, () => {
    void stopInbound().finally(() => server.close(() => process.exit(0)));
  });
}
