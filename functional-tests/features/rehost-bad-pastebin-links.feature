Feature: The bot rehosts annoying pastebin links on good pastebins

    Scenario: Rehosting annoying pastebin links

        Given a user is in the channel

        When the user sends a message with an annoying pastebin link

        Then the bot downloads the content
            And the bot uploads the content to a good pastebin
            Then the bot posts the rehosted link in the channel
            And the rehosted link contains the same content as the original


    Scenario: Rehosting multiple pastebin links on a single good pastebin

        Given a user is in the channel

        When the user sends a message with two annoying pastebin links

        Then the bot downloads the content from all links
            And the bot uploads the content to a good pastebin
            Then the bot posts the rehosted link in the channel
            And the rehosted link contains all the content from the originals
