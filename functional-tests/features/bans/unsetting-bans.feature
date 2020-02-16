Feature: The bot assists with unsetting bans

    Scenario: Recording an unset ban

        Given a chanop is in the channel
            And a ban is set on the channel

        When the chanop unsets the ban

        Then the bot notifies the chanop
            And the message says when the ban was set and by whom
            And the ban will show as expired in the webui.

    Scenario: Automatically unsetting an expired ban

        Given a ban is set on the channel
            And the ban has an expiration set

        When the expiration time passes

        Then the bot will unset the ban
            And the ban will show as expired in the webui.
