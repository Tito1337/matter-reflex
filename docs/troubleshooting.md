# Troubleshooting a Reflex automation

| What you see | What to check |
| --- | --- |
| Home Assistant discovers devices, but the Reflex setup form lists no boards | Check that the XIAO has been commissioned through this Home Assistant instance’s Matter integration and is running the Reflex firmware. The USB console should show `thread=attached` and a Matter fabric. |
| **Synchronized** is on, but a shortcut is missing | Open **Settings → System → Repairs**. If Reflex rejects an automation, it excludes the entire automation from the synchronized table. Check the automation’s label, whether it is enabled, and the event type the button advertises. |
| **Synchronized** stays off | Open **Repairs** to see why synchronization failed. Check that Matter Server and the affected endpoints are available. If an access control list (ACL) update was rejected, check for a full list or concurrent changes before editing permissions. |
| A press during an outage does nothing | Run `./dev usb python /work/tools/board_console.py status`. Wait for `active=1` before pressing the button. Read the execution counter immediately before and after the gesture. If the server recovered automatically, it may have renewed the lease. |
| The counter increases, but the target does not respond | The counter includes failed executions and counts each target’s rule separately. Check the `RuleExecuted` status if your controller exposes it. Local status codes `0x100`, `0x101`, and `0x102` indicate a missing session, an unknown state, and a transport error, respectively. |
| The XIAO is slow to take over after losing radio connectivity | The firmware keeps sessions and the peer address in RAM. After a restart or loss of a route, reconnection depends on Thread connectivity and the discovery services that are still available. |

## Downloading Home Assistant diagnostics

Go to **Settings → Devices & services → Matter Reflex**, open the integration entry’s menu, and select **Download diagnostics**. The `data` section shows which transport synchronizes the rules (`matter` or `http`), whether the latest status is available, whether Reflex is active, whether the rules are synchronized, and how many rule results the XIAO has reported since startup. A value of `null` means the information is unavailable. During a server outage, you can read the board’s status directly over USB.

For a public issue, attach the `data` section. Home Assistant adds metadata and repair details to the full file, which may include names or identifiers from your installation.

## Verifying an outage test

Choose a target whose state change you can see. Record its initial state, then read the rule table fingerprint and execution counter over USB. During a controlled outage, wait for `active=1`, press the button, and observe the target. After recovery, check that the board returns to `active=0`, Home Assistant shows the rules as synchronized, and the device is available again.

One press that controls two lights and a shade can produce three results. If the counter increases by more than expected, record the button presses and their times before and after recovery. The counter alone cannot tell you which gesture produced a particular result.
