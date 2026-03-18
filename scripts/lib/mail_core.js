/**
 * apple mail JXA core library
 *
 * batch-optimized Mail.app automation via JavaScript for Automation.
 * injected into all JXA scripts to provide consistent account resolution,
 * case-insensitive mailbox lookup, and batch property fetching.
 */

const Mail = Application("Mail");

const _SYSTEM_MAILBOX_NAMES = new Set([
    "calendar", "contacts", "tasks", "journal", "notes",
    "outbox", "conversation history", "rss feeds", "sync issues",
    "sync issues/conflicts", "sync issues/local failures",
    "sync issues/server failures", "suggested contacts",
    "recipientcache", "personmetadata",
]);

function _isEmailMailbox(name) {
    return !_SYSTEM_MAILBOX_NAMES.has(name.toLowerCase());
}

const MailCore = {
    getAccountByEmail(email) {
        const accounts = Mail.accounts();
        if (!email) {
            if (accounts.length === 0) throw new Error("no mail accounts configured");
            return accounts[0];
        }
        const target = email.toLowerCase();
        const allEmails = Mail.accounts.emailAddresses();
        for (let i = 0; i < accounts.length; i++) {
            for (let j = 0; j < allEmails[i].length; j++) {
                if (allEmails[i][j].toLowerCase() === target) return accounts[i];
            }
        }
        throw new Error("no account found for email: " + email);
    },

    getAccountByName(name) {
        if (!name) {
            const accounts = Mail.accounts();
            if (accounts.length === 0) throw new Error("no mail accounts configured");
            return accounts[0];
        }
        return Mail.accounts.byName(name);
    },

    getMailbox(account, name) {
        const target = name.toLowerCase();
        var mboxes = account.mailboxes();
        var names = account.mailboxes.name();
        for (var i = 0; i < names.length; i++) {
            if (names[i].toLowerCase() === target) return mboxes[i];
        }
        for (var i = 0; i < mboxes.length; i++) {
            if (!_isEmailMailbox(names[i])) continue;
            try {
                var children = mboxes[i].mailboxes();
                var childNames = mboxes[i].mailboxes.name();
                for (var c = 0; c < childNames.length; c++) {
                    if (childNames[c].toLowerCase() === target) return children[c];
                }
            } catch(e) {}
        }
        throw new Error("mailbox not found: " + name);
    },

    batchFetch(msgs, props) {
        const result = {};
        for (const prop of props) {
            result[prop] = msgs[prop]();
        }
        return result;
    },

    /**
     * Fetch properties for only the first `limit` messages.
     * Uses per-message access to avoid loading entire mailbox into memory.
     * Falls back to batchFetch when limit >= total (no savings from per-message).
     */
    fetchLimited(mbox, props, limit) {
        const ids = mbox.messages.id();
        const total = ids.length;
        const count = Math.min(total, limit);
        if (count >= total) return MailCore.batchFetch(mbox.messages, props);
        const result = {};
        for (const prop of props) result[prop] = [];
        result.id = [];
        for (let i = 0; i < count; i++) {
            var msg = mbox.messages[i];
            result.id.push(ids[i]);
            for (const prop of props) {
                if (prop === "id") continue;
                try { result[prop].push(msg[prop]()); }
                catch(e) { result[prop].push(null); }
            }
        }
        return result;
    },

    formatDate(date) {
        if (!date || !(date instanceof Date)) return null;
        return date.toISOString();
    },

    today() {
        const d = new Date();
        d.setHours(0, 0, 0, 0);
        return d;
    },

    daysAgo(days) {
        const d = new Date();
        d.setDate(d.getDate() - days);
        d.setHours(0, 0, 0, 0);
        return d;
    },

    listAccounts() {
        const names = Mail.accounts.name();
        const users = Mail.accounts.userName();
        const emails = Mail.accounts.emailAddresses();
        const results = [];
        for (let i = 0; i < names.length; i++) {
            results.push({
                name: names[i],
                user: users[i],
                emails: emails[i].join(",")
            });
        }
        return results;
    },

    listMailboxes(account) {
        const names = account.mailboxes.name();
        const results = [];
        for (let i = 0; i < names.length; i++) {
            results.push({ name: names[i] });
        }
        return results;
    },

    listMailboxesWithCounts(account) {
        const results = [];
        function walk(mboxes, prefix) {
            const names = mboxes.name();
            for (let i = 0; i < names.length; i++) {
                const fullPath = prefix ? prefix + "/" + names[i] : names[i];
                let count = 0;
                try { count = mboxes[i].messages.id().length; } catch(e) {}
                results.push({
                    folder_name: names[i],
                    folder_path: fullPath,
                    email_count: count
                });
                let children;
                try { children = mboxes[i].mailboxes; if (children.length > 0) walk(children, fullPath); } catch(e) {}
            }
        }
        walk(account.mailboxes, "");
        return results;
    },

    getEmailsByIds(mailbox, targetIds) {
        const allIds = mailbox.messages.id();
        const results = [];
        for (const tid of targetIds) {
            const idx = allIds.indexOf(tid);
            if (idx !== -1) results.push(mailbox.messages[idx]);
        }
        return results;
    },

    findMessageById(account, targetId) {
        const mboxes = account.mailboxes();
        const names = account.mailboxes.name();
        const inboxFirst = [];
        const rest = [];
        for (let i = 0; i < names.length; i++) {
            if (!_isEmailMailbox(names[i])) continue;
            if (names[i].toLowerCase() === "inbox") inboxFirst.push(mboxes[i]);
            else rest.push(mboxes[i]);
        }
        const ordered = inboxFirst.concat(rest);
        for (const mb of ordered) {
            let ids;
            try { ids = mb.messages.id(); } catch(e) { continue; }
            const idx = ids.indexOf(targetId);
            if (idx !== -1) return mb.messages[idx];
        }
        return null;
    },

    findMessageAcrossAccounts(targetId) {
        const accounts = Mail.accounts();
        for (const acc of accounts) {
            const msg = MailCore.findMessageById(acc, targetId);
            if (msg) return msg;
        }
        return null;
    },

    /**
     * Resolve a list of integer IDs to their RFC Message-ID headers.
     * Returns {intId: rfcMessageId, ...} for all found messages.
     */
    resolveMessageIds(integerIds) {
        const result = {};
        const remaining = new Set(integerIds);
        const accounts = Mail.accounts();
        for (let a = 0; a < accounts.length && remaining.size > 0; a++) {
            const mboxes = accounts[a].mailboxes();
            const mboxNames = accounts[a].mailboxes.name();
            for (let m = 0; m < mboxes.length && remaining.size > 0; m++) {
                if (!_isEmailMailbox(mboxNames[m])) continue;
                let ids;
                try { ids = mboxes[m].messages.id(); } catch(e) { continue; }
                const found = [];
                for (const tid of remaining) {
                    const idx = ids.indexOf(tid);
                    if (idx !== -1) found.push({ tid: tid, idx: idx });
                }
                if (found.length > 0) {
                    let rfcIds;
                    try { rfcIds = mboxes[m].messages.messageId(); } catch(e) { rfcIds = []; }
                    // Array length consistency check: skip if mailbox mutated between fetches
                    if (rfcIds.length !== ids.length) { continue; }
                    for (const f of found) {
                        result[String(f.tid)] = rfcIds[f.idx] || "";
                        remaining.delete(f.tid);
                    }
                }
            }
        }
        return result;
    },

    /**
     * Resolve integer IDs to metadata: RFC Message-ID, subject, sender, flagged.
     * Returns {intId: {rfcId, subject, sender, flagged}, ...}
     */
    resolveMessageDetails(integerIds) {
        const result = {};
        const remaining = new Set(integerIds);
        const accounts = Mail.accounts();
        for (let a = 0; a < accounts.length && remaining.size > 0; a++) {
            const mboxes = accounts[a].mailboxes();
            const mboxNames = accounts[a].mailboxes.name();
            for (let m = 0; m < mboxes.length && remaining.size > 0; m++) {
                if (!_isEmailMailbox(mboxNames[m])) continue;
                let ids;
                try { ids = mboxes[m].messages.id(); } catch(e) { continue; }
                const found = [];
                for (const tid of remaining) {
                    const idx = ids.indexOf(tid);
                    if (idx !== -1) found.push({ tid: tid, idx: idx });
                }
                if (found.length > 0) {
                    let rfcIds, subjects, senders, flagged;
                    try { rfcIds = mboxes[m].messages.messageId(); } catch(e) { rfcIds = []; }
                    try { subjects = mboxes[m].messages.subject(); } catch(e) { subjects = []; }
                    try { senders = mboxes[m].messages.sender(); } catch(e) { senders = []; }
                    try { flagged = mboxes[m].messages.flaggedStatus(); } catch(e) { flagged = []; }
                    // Array length consistency check: if any property array length
                    // differs from ids, the mailbox mutated between fetches — skip
                    // to avoid index misalignment (wrong RFC ID for wrong email).
                    if (rfcIds.length !== ids.length || subjects.length !== ids.length ||
                        senders.length !== ids.length || flagged.length !== ids.length) {
                        continue; // mailbox mutated during fetch — skip, will retry via other mailbox or remain unresolved
                    }
                    for (const f of found) {
                        result[String(f.tid)] = {
                            rfcId: (rfcIds[f.idx] || ""),
                            subject: (subjects[f.idx] || ""),
                            sender: (senders[f.idx] || ""),
                            flagged: (flagged[f.idx] || false)
                        };
                        remaining.delete(f.tid);
                    }
                }
            }
        }
        return result;
    },

    /**
     * Find a message by its RFC Message-ID header across all accounts.
     * Returns the message object or null.
     */
    findMessageByRfcId(rfcMessageId) {
        const accounts = Mail.accounts();
        for (let a = 0; a < accounts.length; a++) {
            const mboxes = accounts[a].mailboxes();
            const mboxNames = accounts[a].mailboxes.name();
            for (let m = 0; m < mboxes.length; m++) {
                if (!_isEmailMailbox(mboxNames[m])) continue;
                let rfcIds;
                try { rfcIds = mboxes[m].messages.messageId(); } catch(e) { continue; }
                const idx = rfcIds.indexOf(rfcMessageId);
                if (idx !== -1) return mboxes[m].messages[idx];
            }
        }
        return null;
    }
};
