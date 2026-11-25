# Setting up WB VoIP

This guide covers the setup of `wb_voip`, `wb_voip_crm` and `wb_voip_ansitel` in Odoo 19. It is written for the person who
runs Odoo (access to the server and to system administration). The German version is
[einrichtung.md](einrichtung.md). Features and status: [README](../README.md) (German).

Example addresses in this guide (`pbx.example.com`, numbers in the range `+49 30 1234567`) are fictitious.

## 1. Requirements

| | |
|---|---|
| Odoo | 19.0, Community or Enterprise. On Enterprise the Odoo module `voip` must not be installed (`wb_voip` excludes it). |
| Odoo modules | `mail`, `phone_validation`, `bus`, `web` (standard), `wb_license_client` (license client); `wb_voip_crm` also needs `crm` |
| Python | `cryptography` (included in the official Odoo image); `phonenumbers` for phone number validation (used by `phone_validation`, recommended) |
| Odoo address | **HTTPS with a valid certificate.** Browsers only grant microphone and WebRTC access in a secure context. |
| PBX | SIP over WebSocket with TLS (`wss://`, RFC 7118) and WebRTC-capable extensions, one extension per user |
| Network | Every user's browser can reach the PBX `wss://` address and its media ports. |
| ICE | Optional. Only `stun:` URLs are supported; TURN is not supported. With no entry the browser does not query a third-party STUN server. |
| Browser | current Chrome, Edge or Firefox |
| License | a valid license key for product code `VOIP` |

## 2. Installation

1. Put `wb_voip` and `wb_license_client` into the addons path. Add `wb_voip_crm` for the CRM integration and
   `wb_voip_ansitel` for an Ansitel PBX.
2. Update the app list (developer mode) and install **WB VoIP**. `wb_license_client` is installed as a dependency.
3. `wb_voip_crm` installs itself as soon as `crm` and `wb_voip` are installed (`auto_install`). It needs no setup of its own.
4. `wb_voip_ansitel` does not install itself. You only need it if your PBX is from Ansitel (section 10).

The menus under Settings › Technical only show in developer mode (Settings › "Activate the developer mode").

When `wb_voip` is installed, the users `base.user_root` and `base.user_admin` are added once to the group "WB VoIP:
Administrator". You assign everyone else yourself (section 6).

## 3. Activate the license

`wb_voip` checks the license for product code `VOIP` through `wb_license_client`. To activate (system administrators only):

1. Settings › **WB Lizenzen** (license) › **Activate license**.
2. Enter product code `VOIP`, the license key (format `WB-XXXX-…`) and the activation code (25 characters in groups of
   five, from the portal download). The API secret (`WBS-…`) is optional and comes with the key if one was issued.
   Tick the required confirmations (EULA, terms, privacy notice) and confirm.
3. Check under **License status** that the license is "active". A daily job syncs it with the license server; if the
   server is briefly unreachable, the license keeps working at first.

Test systems (a database that is not the licensed production instance) run on a time-limited trial; after that the
database needs a DEV license, and a copied database should be neutralized. After moving to another domain or database,
request the switch under **Request migration**. License questions: support@wissen-beratung.de.

Without a valid license the softphone shows "License missing or expired". Calling and registering with the PBX are then
blocked; lists, forms and existing call logs stay readable and editable.

## 4. Server key `WB_VOIP_FERNET_KEY`

WB VoIP stores SIP passwords (and, for Ansitel, API tokens) only as Fernet ciphertext. The key is **not** kept in Odoo, only
in the environment of the Odoo process.

1. Generate a key:

   ```bash
   python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

2. Set the result as environment variable `WB_VOIP_FERNET_KEY` for the Odoo process (systemd unit, container environment or
   however you manage environment variables) and restart Odoo. With several Odoo workers or servers on the same database,
   every one needs the same key.
3. Keep the key secret: never in the database, system parameters, logs, tickets or a repository. Keep a copy in the
   operator's password store.

If the key is missing, no password can be saved and the softphone reports that it is not configured on the server.

**Changing or losing the key:** stored ciphertext cannot be read with a new key. All SIP passwords must then be entered
again (each user under My Profile › Telephony, or a VoIP administrator on the user); for Ansitel also the central token and
all personal tokens. Only change the key deliberately.

## 5. Create the PBX

Settings › Technical › WB VoIP › **PBXs**. Only system administrators may create a PBX or change its connection data: the
browser logs in at the stored address, so a wrong address would hand every user's credentials to a third party.

| Field | Meaning |
|---|---|
| Name | free text |
| WebSocket URL | must start with `wss://`, e.g. `wss://pbx.example.com/ws` |
| SIP domain | e.g. `pbx.example.com` |
| Country | counts as "domestic" when forwarding targets are checked (Ansitel) and for number normalization |
| ICE servers (STUN) | optional, one URL per line, `stun:` only, e.g. `stun:stun.example.org:3478`, at most 10 lines. Empty is enough if the PBX relays media itself. |
| Mode | **Test** simulates the far end without a real PBX (for trying out); **Production** registers with the PBX |
| Pick-up prefix | optional dial prefix of the PBX for picking up a colleague's ringing call (e.g. `**` on Asterisk-based PBXs), at most 8 characters from `0-9`, `*`, `#`; empty = pick-up off |

A VoIP administrator may also change name, mode and pick-up prefix. New PBXs start in "Test"; switch to "Production" only
after the users are set up.

## 6. Set up users

1. **Assign the group:** user form › access rights: "WB VoIP: User" for everyone who phones, "WB VoIP: Administrator" for
   managing PBXs, extensions and settings. The administrator group includes the user group.
2. **Extension:** user form, tab "Telephony" (only VoIP administrators may change it; everyone else sees the fields
   read-only): choose the PBX and the SIP user (the extension on the PBX). Each extension belongs to exactly one user and
   one PBX.
3. **SIP password:** the user sets it under My Profile › **Telephony** ("SIP password"), or a VoIP administrator on the
   user form. The field is write-only, never readable; "Saved – enter again to change" shows that one exists. It is
   decrypted only when the user logs in to the PBX.
4. The user reloads Odoo. The phone icon in the top bar shows the state: green connected, grey off, yellow in another tab,
   red disconnected, without license or not set up. On the first incoming call the browser asks for notification and
   microphone permission.

## 7. Rights

Checked against the access rights (`ir.model.access.csv`), record rules and the checks in code. "VoIP administrator" is
the group "WB VoIP: Administrator". **System administrators (group "Administration: Settings") do not include the VoIP
groups.** Whoever needs both belongs to both groups; only `admin` and the system user are added to the VoIP group on
installation.

| Action | User | VoIP administrator | System administrators |
|---|---|---|---|
| Use the softphone, place and take calls | yes (with license) | yes | only with a VoIP group |
| See, create, edit own calls and call logs | yes | yes | only with a VoIP group |
| See other users' calls and call logs | no | yes, all | only with a VoIP group |
| Delete calls and call logs | no | yes | only with a VoIP group |
| Set own SIP password | yes | yes | only with a VoIP group |
| Set another user's SIP password | no | yes | only with a VoIP group |
| Read a SIP password | never | never | never (only ciphertext in the database) |
| Set PBX and extension on a user | no (read only) | yes | only with a VoIP group |
| Read a PBX | yes | yes | yes |
| PBX: change name, mode, pick-up prefix | no | yes | yes |
| PBX: create, delete; change WebSocket URL, SIP domain, ICE servers, country | no | no | yes |
| View call buttons and wizard buttons | yes (read only) | yes, create, edit, delete | only with a VoIP group |
| Create or change buttons with Python code (wizard) | no | no | yes |
| Window layouts and layout designer | no | yes (menu needs access to Settings) | only with a VoIP group |
| Set retention period, team overview, call duration | no | yes (the settings page needs access to Settings) | only with a VoIP group |
| Activate license, request migration | no | no | yes |
| Ansitel: interface, URL, central token, time zone | no | no | yes |
| Ansitel: read the extension on a user | no | yes | yes |
| Ansitel: set extension, personal token, voicemail release | no | no | yes |
| Ansitel: read devices | no | yes | yes |
| Ansitel: change devices, "Load devices from PBX", additional internal targets | no | no | yes |
| Ansitel: view keys | yes (read only) | yes, maintain label, kind, order | only with a VoIP group |
| Ansitel: set fixed and internal key targets | no | no | yes |
| Ansitel: "Test connection" | no | yes | yes |
| Ansitel: switch keys, set "My mobile phone" | yes, for themselves only | yes | yes |
| CRM: see calls and call logs on the opportunity | own | all | only with a VoIP group |
| CRM: read the technical field call → opportunity | no | no | yes |

Note on menus: the "WB VoIP" menu under Settings › Technical is visible to VoIP administrators, but only in developer mode.

## 8. Team overview, privacy and works council

The "Team" tab in the softphone shows colleagues as free, on a call, ringing or logged out. This is processing of employees'
personal data (presence and phone status).

- **Default: on.** Since version 19.0.4.5.0 the overview is on for all companies; the update switches it on for existing
  companies. Call duration is **off** by default.
- **The operator is responsible.** Privacy-friendly defaults (Art. 25 GDPR) are the operator's duty: switch the overview
  **off until employees have been informed** (information duty, Art. 13 GDPR), and check whether a data protection impact
  assessment (Art. 35 GDPR) is needed.
- **Works council.** Where a works council exists, introducing a technical system that can monitor behavior or performance
  requires its co-determination (§ 87 (1) no. 6 BetrVG, German Works Constitution Act). Use the overview only after its
  consent (usually a works agreement); switch it off until then. The same applies to the Ansitel call-list sync (section 10).
- **Switches per company:** Settings › **WB VoIP** › "Show team overview" and "Show call duration in the team". The company
  the person is currently working in (active company) decides. Switching off deletes the stored heartbeats of all users of
  that company.
- **What colleagues see:** only name, extension, status and, for "on a call" (if enabled), the duration; never the other
  number, contact or note. As presence, Odoo stores only the time of the phone tab's last heartbeat; after 150 seconds
  without one the colleague counts as "logged out", and it is deleted on logout.
- **Missed calls:** colleagues in the same company see name, duration and note of the person who took a call. The note
  field of incoming calls says so.

**Retention period.** Settings › **WB VoIP** › "Retention period for call data" (system parameter
`wb_voip.aufbewahrung_monate`): default **12 months**, `0` = never delete. A daily job removes finished calls older than
that; call logs are kept. Set the period to match your deletion policy and record it in your record of processing
activities (Art. 30 GDPR).

Buttons of type "Web address" pass the phone number and contact ID to the operator of that address: for external services
conclude a data processing agreement (Art. 28 GDPR) and, for providers outside the EU/EEA, check the transfer to third
countries.

## 9. CRM integration (`wb_voip_crm`)

No setup of its own; rights and PBX follow `wb_voip`.

- When a call starts it is assigned to the **open opportunity**: active, probability below 100 %, in the current company or
  without a company, on the contact or on its company. The search runs with the user's rights.
- **Assignment rule:** an opportunity the call was started from takes precedence. Otherwise, with several candidates, the
  most recently changed one among those assigned to the caller wins; if there is none of their own, none is assigned
  (better none than a wrong one). Assign and unlink manually in the "Opportunities" block of the caller window.
- On the opportunity: smart button "Calls" and tab "Call logs", each limited to the user's rights.
- **Callback activity** for missed calls on the open opportunity (otherwise on the contact).
- **Call button "Create opportunity"** (Settings › Technical › WB VoIP › Call buttons) with a title template (`{kontakt}`,
  `{nummer}`, `{datum}`) and optionally a sales team; created with the user's rights.
- Known limit: opportunities on employees of a company contact are not found when the company itself calls.

## 10. Ansitel integration (`wb_voip_ansitel`)

For Ansitel PBXs through their REST interface: quick keys (call forwarding, Do not disturb, voicemail) and sync with the
PBX call list. System administrators do all steps except the test.

1. **Interface:** on the PBX tick "Ansitel interface active", enter the REST URL (`https` only, e.g.
   `https://pbx.example.com/awi/api/rest`) and the **central token** (stored only as ciphertext, never shown). Check the
   call-list time zone (default Europe/Berlin). Then "Test connection" (reads the device list).
2. **Extensions:** on the user (tab "Telephony") set the **Ansitel extension**; unique per PBX. Anyone who is to switch
   voicemail through their own access gets a **personal token**. Changing a user's PBX deletes their extension and
   personal token; if someone without system rights changes a PBX's URL, its tokens are deleted.
3. **Voicemail token:** order: personal token; otherwise the central token if system administrators ticked "Switch
   voicemail through the central token" on the user and the extension belongs to the central token; otherwise the central
   token if it has exactly one extension and it is the user's. Otherwise the voicemail key stays grey with a hint.
4. **Devices:** on the PBX press "Load devices from PBX" (creates missing devices from the device list) and check the
   assignment to users (Settings › Technical › WB VoIP › Devices). **Never enter the trunk as a device.**
5. **Keys:** Settings › Technical › WB VoIP › Softphone keys. Shipped: "→ Handy" (to mobile), "Do not disturb" and
   voicemail. Each user enters "My mobile phone" under My Profile › Telephony. Display per user ("Quick keys" setting): in
   the softphone, as a narrow bar on the edge, or both; the change takes effect after a reload. Voicemail, forwarding and
   Do not disturb are mutually exclusive.

### Forwarding targets (toll fraud protection)

Forwarding makes the PBX dial out at the line owner's expense. WB VoIP therefore checks every target when saving and each
time a key is switched:

- digits 0–9 only, at least 6 digits, a valid phone number; the normalized form (`+49…`) is stored;
- no emergency, short-code or directory numbers (110, 112, 115, 116xxx, 118xx);
- no special-rate and premium numbers (Germany among others 0137, 0138, 0180–0194, 0700, 0900; Austria 0900, 0901, 0930,
  0939; Switzerland 0900, 0901, 0906); satellite services (+881, +882, +883) are always blocked;
- targets with a country code other than the PBX's country only if the system parameter
  **`wb_voip_ansitel.ausland_erlaubt` is `1`** (default: blocked). The premium and emergency blocks still apply, but foreign
  premium numbers are not excluded by that.

**Internal extension or ring group** (target kind "Internal extension"): 2 to 6 digits, no leading 0, no emergency/short
numbers. Valid only if the target is listed under "Additional internal targets" on the PBX (ring groups, queues; one
number per line) or the PBX knows a peer with exactly that internal number. The device list is cached for up to
10 minutes. The user's own extension is blocked as a target. A ring group the user is a member of is not detected as a
loop.

Only system administrators set fixed and internal key targets; VoIP administrators maintain label, kind and order. Dialing
uses digits only; a leading "+" becomes "00".

### Call list sync

The cron "WB VoIP Ansitel: Abgleich mit der Anrufliste" (call list sync) runs every 5 minutes for each active PBX with a
token and reads the call list (`getcdr`, at most 1000 rows per request). Calls Odoo did not see (Odoo was closed) and calls
answered on a desk phone or through forwarding appear as "answered elsewhere" with device and duration.

- Window: from = state − 4 hours, to = now − 60 seconds, at most 6 hours per run; the first run reads the last 30 minutes.
- The sync also runs without a valid license (it only reads and never switches anything on the PBX).
- Limits: if the PBX returns more than 50 pages per request, the window is halved; if that is not enough, that piece is
  skipped (error in the log; those calls are then missing). The doubled hour in autumn is only considered when matching
  softphone calls. A mobile voicemail behind a forwarding counts as forwarding. The team status of desk phones does not come
  from the PBX and is therefore missing.
- Privacy: time, duration, device and the other number are imported (also for unknown numbers, unassigned). The retention
  period from section 8 applies. Inform employees (Art. 13 GDPR) and respect co-determination (§ 87 (1) no. 6 BetrVG).
- After setup, watch the first runs; errors are in the Odoo log (identifiers and error types only, never token or URL).

## 11. Troubleshooting

| Symptom | Cause and remedy |
|---|---|
| Icon yellow, "Phone is running in another tab" | The PBX accepts only one registration per extension, so only one Odoo tab holds the phone. Other tabs hand dialing over to it. If that tab closes, the next one takes over. The browser must support `navigator.locks` and `BroadcastChannel`. |
| Icon red, "Not connected", registration rejected | Wrong SIP password or extension, the extension is registered elsewhere, or the PBX refuses the registration. Re-enter the password and check the PBX log. WB VoIP retries after 3, 10, 30 seconds and then every minute; "Reconnect" in the softphone retries at once. |
| Immediately "Not connected" with no PBX message | `wss://` address wrong or unreachable, PBX certificate invalid (see below), firewall or proxy blocks WebSocket. Look at the WebSocket error in the browser console (network tab). |
| "Telephony is not set up yet." or "The softphone is not configured on the server." | Second message: `WB_VOIP_FERNET_KEY` is missing from the Odoo process (section 4). First message: telephony is not fully set up for the user (section 6). |
| "The phone is not set up for this user." | Enter PBX and SIP user on the user; check the group "WB VoIP: User". |
| No audio or one-way audio | Media is not getting through (ICE). Check: microphone allowed in the browser; the PBX relays media itself or a reachable `stun:` server is entered on the PBX; the firewall allows the media ports (UDP, the PBX's range). TURN is not supported: behind symmetric NAT with no media relay on the PBX, WebRTC will not work. |
| Certificate error on `wss://` | The browser checks the PBX TLS certificate like any website: valid, not expired, name matches the URL host, complete chain. Self-signed certificates are rejected. Renew the certificate on the PBX; to test, open the `https://` address of the same host in the browser. |
| No microphone prompt, or "browser does not support the softphone" | Odoo is not served over HTTPS (no secure context) or the browser is too old. |
| "License missing or expired" | License not activated, expired, or the database does not match the licensed instance (section 3). Calling and registering are blocked, call logs stay readable and editable. Ansitel keys stay grey. License status under Settings › WB Lizenzen shows the license server's message. |
| Ansitel: keys grey, hint that the PBX is unreachable | Check URL and token on the PBX, "Test connection". `https` only with a valid certificate; 10 s timeout, no redirects, no proxies. After PBX errors no further calls are made until the PBX answers. |
| Ansitel: key grey with "Please enter your mobile phone under My Profile › Telephony." | "My mobile phone" is missing or fails the target check (premium, emergency, foreign number; section 10). |
| Ansitel: voicemail key grey | No personal token and no release for the central token (section 10, step 3). |
| Ansitel: calls missing from the sync | Is the cron active? Check extension and device assignment on the user, the call-list time zone, and search the log for skipped windows. |
| Team shows "The team overview is switched off." | Switch per company under Settings › WB VoIP (mind the active company). |
| A call rang but nothing under "Missed" | If no Odoo tab with the phone is open, Odoo does not learn of the call (except through the Ansitel sync). If the tab is closed while ringing, the cleanup job ends the call after 4 hours as "failed". |

For license or product problems: support@wissen-beratung.de. The Odoo log never contains credentials; for a bug report the
time, user and the softphone message are enough.
