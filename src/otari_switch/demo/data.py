"""Sample tickets and the answers a frontier model gave for them. Used to build the demo recordings."""

TICKETS = [
    {
        "text": "Hi, I'm Priya Nair. My order AC-10422 for the Acme Kettle Pro arrived with a cracked lid. "
                "I'd like a replacement or my money back. You can reach me at priya.nair@example.com.",
        "summary": "Priya Nair received the Acme Kettle Pro (order AC-10422) with a cracked lid and wants a "
                   "replacement or a refund.",
        "priority": "high",
        "fields": {"customer_name": "Priya Nair", "product": "Acme Kettle Pro", "order_id": "AC-10422",
                   "issue_type": "defect"},
        "reply": "Hi Priya,\n\nThank you for letting us know, and I'm sorry your Acme Kettle Pro arrived with a "
                 "cracked lid. That is not the experience we want for you. We can send a replacement right away "
                 "or refund order AC-10422 in full, whichever you prefer. Just reply with your choice and we will "
                 "take care of it within one business day. There is no need to return the damaged kettle.\n\n"
                 "Best wishes,\nThe Acme Support Team",
        "action": {"name": "refund_order", "arguments": {"order_id": "AC-10422", "reason": "Arrived damaged"}},
    },
    {
        "text": "I was charged twice for my Acme Cloud subscription this month. Please fix this. - Marco Rossi",
        "summary": "Marco Rossi was billed twice this month for his Acme Cloud subscription and wants the "
                   "duplicate charge fixed.",
        "priority": "high",
        "fields": {"customer_name": "Marco Rossi", "product": "Acme Cloud subscription", "order_id": None,
                   "issue_type": "billing"},
        "reply": "Hi Marco,\n\nThank you for reaching out, and sorry about the double charge on your Acme Cloud "
                 "subscription. I've passed this to our billing team, who will confirm the duplicate payment and "
                 "refund it to your original payment method. You'll get an email as soon as it's done, usually "
                 "within two business days.\n\nKind regards,\nThe Acme Support Team",
        "action": {"name": "escalate_to_human", "arguments": {"team": "billing",
                                                              "note": "Customer charged twice this month"}},
    },
    {
        "text": "Where is my package? Order AC-10988 was supposed to arrive last Friday. Tracking hasn't moved "
                "in 5 days. Sam Okafor",
        "summary": "Sam Okafor's order AC-10988 is late; it was due last Friday and tracking has not updated "
                   "for five days.",
        "priority": "medium",
        "fields": {"customer_name": "Sam Okafor", "product": "unknown", "order_id": "AC-10988",
                   "issue_type": "delivery"},
        "reply": "Hi Sam,\n\nThanks for your patience, and sorry that order AC-10988 hasn't arrived yet. I've "
                 "opened an investigation with our courier, since the tracking hasn't changed in five days. "
                 "We'll update you within 48 hours, and if the parcel is lost we'll send a replacement at no "
                 "cost.\n\nBest,\nThe Acme Support Team",
        "action": {"name": "escalate_to_human", "arguments": {"team": "technical",
                                                              "note": "Tracking stalled for 5 days on AC-10988"}},
    },
    {
        "text": "How do I reset my Acme account password? The reset email never comes. - Lena Fischer",
        "summary": "Lena Fischer cannot reset her Acme account password because the reset email is not "
                   "arriving.",
        "priority": "medium",
        "fields": {"customer_name": "Lena Fischer", "product": "Acme account", "order_id": None,
                   "issue_type": "account"},
        "reply": "Hi Lena,\n\nThank you for getting in touch. Reset emails sometimes land in spam or promotions "
                 "folders, so please check there first. If it still isn't there, reply to this message and we'll "
                 "reset your password manually and confirm by email.\n\nWarm regards,\nThe Acme Support Team",
        "action": {"name": "send_kb_article", "arguments": {"topic": "password reset email not arriving"}},
    },
    {
        "text": "My Acme Heater X2 started smoking and smells like burning plastic! I unplugged it. Order "
                "AC-11230. Call me on +44 7700 900123. - Tom Becker",
        "summary": "Tom Becker reports his Acme Heater X2 (order AC-11230) started smoking and smelled of burning "
                   "plastic; he has unplugged it.",
        "priority": "urgent",
        "fields": {"customer_name": "Tom Becker", "product": "Acme Heater X2", "order_id": "AC-11230",
                   "issue_type": "defect"},
        "reply": "Hi Tom,\n\nThank you for telling us straight away, and well done for unplugging the heater. "
                 "Please don't use it again. Our safety team will call you today to arrange collection and a full "
                 "refund or replacement for order AC-11230.\n\nStay safe,\nThe Acme Support Team",
        "action": {"name": "escalate_to_human", "arguments": {"team": "safety",
                                                              "note": "Heater X2 smoking, possible fire risk"}},
    },
    {
        "text": "Can I change the delivery address for order AC-11301? I'm moving next week. Thanks, Aiko Tanaka",
        "summary": "Aiko Tanaka wants to change the delivery address for order AC-11301 because she is moving "
                   "next week.",
        "priority": "low",
        "fields": {"customer_name": "Aiko Tanaka", "product": "unknown", "order_id": "AC-11301",
                   "issue_type": "delivery"},
        "reply": "Hi Aiko,\n\nThanks for letting us know in advance. Please reply with your new address and we'll "
                 "update order AC-11301 before it ships. If it has already left our warehouse, we'll ask the "
                 "courier to redirect it.\n\nAll the best,\nThe Acme Support Team",
        "action": {"name": "send_kb_article", "arguments": {"topic": "change delivery address"}},
    },
    {
        "text": "The Acme Blender 500 I bought is louder than advertised. Not broken, just annoying. Any tips? "
                "Order AC-11455, Grace Lee",
        "summary": "Grace Lee finds her Acme Blender 500 (order AC-11455) louder than advertised, though it "
                   "works, and asks for tips.",
        "priority": "low",
        "fields": {"customer_name": "Grace Lee", "product": "Acme Blender 500", "order_id": "AC-11455",
                   "issue_type": "other"},
        "reply": "Hi Grace,\n\nThank you for the feedback on your Acme Blender 500. Placing it on a folded towel "
                 "and starting on a lower speed usually cuts the noise a lot. If it's still too loud, you can "
                 "return it within 30 days of order AC-11455 for a full refund.\n\nCheers,\nThe Acme Support Team",
        "action": {"name": "send_kb_article", "arguments": {"topic": "reduce blender noise"}},
    },
    {
        "text": "I cancelled my Acme Cloud plan in March but I'm still being billed every month. I want all "
                "three payments back. - Daniel Moreau",
        "summary": "Daniel Moreau cancelled his Acme Cloud plan in March but has kept being charged and wants "
                   "the three payments refunded.",
        "priority": "high",
        "fields": {"customer_name": "Daniel Moreau", "product": "Acme Cloud plan", "order_id": None,
                   "issue_type": "billing"},
        "reply": "Hi Daniel,\n\nI'm sorry you've been charged after cancelling your Acme Cloud plan in March. "
                 "I've asked our billing team to confirm the cancellation date and refund all three payments. "
                 "You'll get a confirmation email once the refund is issued, normally within three business "
                 "days.\n\nKind regards,\nThe Acme Support Team",
        "action": {"name": "escalate_to_human", "arguments": {"team": "billing",
                                                              "note": "Billed after cancellation, 3 refunds"}},
    },
]
