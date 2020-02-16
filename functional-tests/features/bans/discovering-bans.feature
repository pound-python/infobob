Feature: The bot discovers bans it did not see happen

    Scenario: Populating the recorded bans

        Given the bot is not in the channel
            And a ban is set on the channel
            And the bot does not know about the ban

        When the bot joins the channel

        Then the ban shows as active in the webui
            And the ban has a "reason" in the webui
            And the reason says the ban was pulled from the channel.
