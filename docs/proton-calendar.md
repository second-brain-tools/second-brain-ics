# Proton Calendar

Proton Calendar has no API and no CalDAV, and Proton Mail Bridge carries mail only. What it does
have is the **share link**: a secret address that serves one calendar as an iCalendar feed.
That is what this server reads. It is read-only by nature: nothing can be written back through it.

## Making a link

Link sharing needs a paid Proton plan, and is done in the Proton Calendar web app (not the mobile
apps): **Settings → All settings → Calendars**, select the calendar, then under **Share with
anyone** choose **Create link**, and pick **Full view** (Proton: titles, places, descriptions and
participants), not "Limited view" (busy times only). Proton allows up to five links per calendar;
make one for this server alone, so it can be deleted without touching any other.

Copy the link straight into the env file, never into a chat or a ticket:

```bash
umask 077
printf 'SBICS_FEED_PERSONAL=%s\n' 'https://calendar.proton.me/api/calendar/v1/url/…' >> ics.env
```

## Deleting a link

The same place: under **Share with anyone**, the link's **Actions** menu → **Delete link**. The
feed then stops answering with the calendar, and the server reports the error it gets (for a
404: "the share link may have been deleted"). Delete links you no longer use: anyone who has one
can read the calendar.

## What the feed holds, and what it doesn't

Measured on 2026-11-21/22 (second-brain-tools/second-brain-joplin#105):

- **It is live.** An event added, changed or deleted in Proton showed in the feed on the next
  one-minute fetch. Proton's own words, that viewers "may take up to eight hours" to see changes,
  describe readers that follow the feed's refresh hint (`REFRESH-INTERVAL:PT4H`); this server
  fetches at most once a minute instead, when asked.
- **No caching headers:** no ETag and no Last-Modified, and `Cache-Control: no-cache, no-store`,
  so each fetch downloads the whole feed (about a second). Twenty fetches thirty seconds apart
  were all answered.
- **It carries:** each event's UID, start, end, repeat rule, cancelled and moved dates, status,
  sequence and title, with each time zone the events use, and a calendar-wide `X-WR-TIMEZONE`.
  `METHOD:PUBLISH`. Whether it carries places, descriptions and attendees was not measured (the
  calendar used had none); Proton's own page says a Full view link shows them.
- **No reminders were seen.** The feed measured carried no `VALARM` at all, so expect
  `reminders` to be empty for a Proton feed (why is not documented).
- **A feed's body can vary while nothing changes.** A brand-new link's first fetch held duplicate
  copies of events, and a first link once returned two different bodies across twenty fetches.
  The server keeps one copy of each event (the highest SEQUENCE, then the latest DTSTAMP), and
  `calendar_list` counts what it dropped.

## What Proton can see

Proton's words: "A URL is generated that contains the key required to decrypt the calendar.
When the URL is used, Proton Mail has access to this key to decrypt the calendar. At no other time
can we access the calendar." This server uses the URL on every fetch (at most once a minute,
when asked), so each fetch gives Proton that key. And anyone who has the link can read the
calendar: treat it like a password.
