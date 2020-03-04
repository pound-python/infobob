Feature: The bot assists with unsetting bans

    Scenario: Automatically unsetting an expired ban

        Given a ban is set on the channel
            And the ban has an expiration set

        When the expiration time passes

        Then the bot will unset the ban
            And the ban will show as expired in the webui.
