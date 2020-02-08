Infobob Behavioral Notes
========================

Mostly for my reference during the maintenance / porting work, but a lot of
this should be a useful starting point for writing actual documentation.

Connecting
----------

Once the connection is established, login is performed by sending in order
PASS (if configured), NICK, USER. This is handled by the base protocol class
(twisted.words.irc.IRCClient). NickServ identification is then performed if
configured.

Periodic tasks are started for:
-   Server ping (this can be removed, heartbeat was added in twisted 11.1's
    IRCClient implementation)
-   Checking and unsetting expired bans
-   Checking for pastebin reachability


Event Responses
---------------

When the bot...

-   completes login or nickserv identification, it joins the channels
    configured as "autojoin".

-   is invited to a channel, it attempts to join it.

-   is kicked from a channel, it attempts to rejoin it.

-   joins a channel that has `anti_redirect` set in the bot's configuration,
    it prevents other join event responses, leaves the channel, and 5 seconds
    later attempts to join the channel specified by the `anti_redirect`
    configuration value. For example, if the configuration has
    `"channels": { "#python-unregistered": { "anti_redirect": "#python" } }`,
    if the bot joins `#python-unregistered`, it will part, wait 5 seconds, and
    attempt to join `#python`.

-   joins a channel that has `has_ops` set to true in the bot's configuration,
    it requests the channel's bans and quiets, and updates its database to
    add bans/quiets that either don't exist or show as expired, with a message
    "ban pulled from banlist on <timestamp>".

    Note: Quiets are handled with the non-standard RPL_QUIETLIST (728) and
    RPL_ENDOFQUIETLIST (729) that freenode's servers implement.

-   joins a channel, it requests the channel's users, and updates its database
    of users and channel members.

-   sees a ban or quiet set, or unset, it updates its database to reflect this.


Ban Management
--------------

In this section, "ban" is either an actual ban (`+b`) or a quiet (`+q`).

For channels configured with `have_ops`, infobob maintains a list of bans with
associated expiration times. When a ban is set, the bot records this with an
expiration timestamp (default is 8 hours after when infobob saw the ban).

Infobob tries to keep this list up to date by listening for bans when bans are
set or unset, and it also reviews the list periodically, unsetting the
channel's ban for those that have expired.

When infobob sees a ban being set, it PMs the op who set the ban, potentially
with some questions:

-   If the ban mask matches a few users, the bot asks for disambiguation.
    The op can choose to select a specific nick, and infobob will unset the
    triggering ban, and set a nick-specific ban in its place. If the op
    rejects this, or doesn't reply for 20 minutes, the bot performs no
    disambiguation.

-   If the ban mask is not an "account" (e.g. `$a:disruptiveUser`, see
    https://freenode.net/kb/answer/extbans), and matches a single user, the bot
    asks if the op wants to change the ban to an account ban. If yes, or if the
    op does not reply for 20 minutes, the bot will unset the original ban and
    set an account ban.

The complexity of the updateBan method is... excessive. There are some other
possibilities that appear to depend on how many users are matched by the mask,
if it's a extban mask, etc. It will take some hard looking to figure it out.

In any event, once the conversation has completed, infobob reports what extra
operations (if any) it performs, and sends a URL for the web UI to the op,
which they can use to add notes and change the expiry.


Utilities
---------

These are all configurable per-channel (on or off), using an ACL-like array in
the channel config object under the key `commands`.


**repaste** feature

If infobob sees a URL from a pastebin deemed "bad", it will attempt to rehost
it on a "good" pastebin, and post in channel e.g. `$URL (repasted for $NICK)`.
The URLs involved are cached in memory, and the bot will only post the rehosted
URL again if enough time has passed since the last occurance.


**lol** feature

When a user says "lol" or something similar, the bot admonishes them.
Irritating, not used any more.


**redent** command

Usage:  `infobob: redent TARGETNICK CODE`

Lexes CODE as Python code, reformats it with indentation, uploads it to a
pastebin, and replies in-channel to TARGETNICK, e.g.
`alice, https://bpaste.net/ABCD`.

The lexer recognizes the first line of "compound statements" (`def`, `class`,
loops, conditionals, `try`, etc), inserts a line break, and increases the
indentation level.

Semicolons are interpreted as line break instructions. A single `;` preserves
the current indentation level, two (`;;`) reduces the indentation level by one,
three (`;;;`) reduces the indentation level by two, and so on.


**stop** command

Usage:  `infobob: stop`

Replies to sending user with "Okay!" and quits.

This should either be removed (since there's no recovery), or adjusted so it
makes the bot just stop performing actions in that channel (for a certain
period?) until resumed by another command. It isn't really used.
