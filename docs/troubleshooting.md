# Troubleshooting a Reflex automation

| What you see | What to check |
| --- | --- |
| Home Assistant discovers devices, but the Reflex setup form lists no boards | Check that the XIAO has been commissioned through this Home Assistant instance’s Matter integration and is running the Reflex firmware. Check the ESPHome USB log for Thread attachment and the commissioned node ID. |
| **Synchronized** is on, but a shortcut is missing | Open **Settings → System → Repairs**. If Reflex rejects an automation, it excludes the entire automation from the synchronized table. All modes require the **reflex** label on the automation. Check whether it is enabled, and the event type the button advertises. |
| **Active** is off for a direct reflex | This indicator shows fallback takeover. Direct reflexes can run while it is off. Check synchronization and the automation’s Activity. |
| No encrypted ESPHome board is available during setup | Add the device to the ESPHome integration with API encryption and the `reflex_exchange` action. Use its current Thread IPv6 address. |
| **Synchronized** stays off | Open **Repairs** to see why synchronization failed. Check that Matter Server and the affected endpoints are available. If an access control list (ACL) update was rejected, check for a full list or concurrent changes before editing permissions. |
| An open door or wet leak detector has no effect on takeover | Check peer readiness. A sensor’s first valid report establishes its baseline. A new transition into the configured state after takeover triggers the command. |
| A press during an outage does nothing | Wait for the XIAO takeover LED or `Lease state: active` in the ESPHome USB log before pressing the button. Look for a rule result in the log. If the server recovered automatically, it may have renewed the lease. |
| The counter increases, but the target does not respond | The counter includes failed executions and counts each target’s rule separately. Check the `RuleExecuted` status if your controller exposes it. Local status codes `0x100`, `0x101`, and `0x102` indicate a missing session, an unknown state, and a transport error, respectively. |
| The XIAO is slow to take over after losing radio connectivity | Check Thread attachment and the peer subscription log. After one confirmed subscription the XIAO can reconnect directly to the saved IPv6 endpoint while discovery is offline. The button and its target each need a working mesh route and a fresh subscription. If an address changed, discovery must find it again. |

## Loading an integration update

After replacing the Python files under `custom_components/matter_reflex`,
restart Home Assistant Core to load the new code. Reloading the integration
or disabling and enabling its entry reruns setup in the same Python process.
For changes to a labeled automation, save it in Home Assistant and allow
Reflex to resynchronize. A Core restart is unnecessary for those changes.

Before the next integration release, investigate how other Home Assistant
custom integrations apply Python updates while Core stays online. Prefer a
supported update method that can demonstrate a change in behavior from the
new code in a test instance while the same Core process keeps running.

## Migrating an HTTP connection

The HTTP client, firmware listener and transfer-key field have been removed. Install the encrypted ESPHome management profile and add its connection to Home Assistant. Then update the integration, restart Core and reconfigure the Matter Reflex entry to select the encrypted board. Follow the [ordered migration steps](../examples/README.md#upgrade-an-http-installation). Existing commissioning data and rules can be retained.

## Downloading Home Assistant diagnostics

Go to **Settings → Devices & services → Matter Reflex**, open the integration entry’s menu, and select **Download diagnostics**. The `data` section shows which transport synchronizes the rules (`matter` or `esphome`), whether the latest status is available, whether Reflex is active, whether the rules are synchronized, and how many rule results the XIAO has reported since startup. A value of `null` means the information is unavailable. During a server outage, the ESPHome USB log continues to report lease changes and the XIAO package logs each rule result.

Home Assistant reads a status snapshot when the board connects and verifies the rule hash after transferring rules. It listens for changes to the hash, active mode and execution count over Matter Server’s existing subscription. Once the server has delivered changes for all three attributes and can read them natively, steady operation needs no periodic status read. If any attribute has yet to report, or the server only supports encrypted ESPHome diagnostics, Home Assistant refreshes the snapshot every five minutes. Unreported changes may take that long to appear.

During a Matter outage, the integration clears its previous status immediately on notification or within five seconds of detecting a closed local WebSocket. If the encrypted ESPHome connection can still reach the board, it reports a new snapshot and checks once more after the lease interval when the board was still in standby. Failed reads show unavailable and retry after one minute. The five-second check reads local client state and sends no network request.

For a public issue, attach the `data` section. Home Assistant adds metadata and repair details to the full file, which may include names or identifiers from your installation.

## Verifying an outage test

Choose a target whose state change you can see. Record its initial state, and the current execution count. During a controlled outage, wait for the `Lease state: active` log message, press the button and observe the target and USB rule result. After recovery, check that the board returns to standby, Home Assistant shows the rules as synchronized, and the device is available again.

One press that controls two lights and a shade can produce three results. If the counter increases by more than expected, record the button presses and their times before and after recovery. The counter alone cannot tell you which gesture produced a particular result.
