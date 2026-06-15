document.addEventListener("DOMContentLoaded", () => {
  const PAYMENT_POLL_INTERVAL_MS = 10000;

  let campaignKey = null;
  let currentPage = null;
  let currentPaymentIntent = null;
  let currentChallengeId = null;
  let paymentPollTimer = null;

  const authScreen = document.getElementById("authScreen");
  const dashboard = document.getElementById("dashboard");
  const authBtn = document.getElementById("authBtn");
  const keyInput = document.getElementById("keyInput");
  const authError = document.getElementById("authError");
  const authErrorText = document.getElementById("authErrorText");
  const fileDrop = document.getElementById("fileDrop");
  const fileInput = document.getElementById("fileInput");
  const photoDrop = document.getElementById("photoDrop");
  const photoInput = document.getElementById("photoInput");
  const postUpdateBtn = document.getElementById("postUpdateBtn");
  const addLinkBtn = document.getElementById("addLinkBtn");
  const applyPromoBtn = document.getElementById("applyPromoBtn");
  const renewBtn = document.getElementById("renewBtn");
  const archivePageBtn = document.getElementById("archivePageBtn");
  const upgradeOptions = document.getElementById("upgradeOptions");
  const paymentCard = document.getElementById("paymentCard");
  const paymentCardBody = document.getElementById("paymentCardBody");

  function showToast(message, isError = false) {
    const toast = document.getElementById("toast");
    if (!toast) {
      return;
    }
    toast.textContent = message;
    toast.className = `toast show${isError ? " error" : ""}`;
    window.setTimeout(() => {
      toast.className = "toast";
    }, 3000);
  }

  function api(method, path, body) {
    return fetch(path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    }).then(async (response) => {
      let data = {};
      try {
        data = await response.json();
      } catch (_error) {
        data = {};
      }
      if (!response.ok) {
        const err = new Error(data.detail || "Request failed");
        err.status = response.status;
        throw err;
      }
      return data;
    });
  }

  function capitalize(value) {
    return value ? value.charAt(0).toUpperCase() + value.slice(1) : "";
  }

  function tierLabel(value) {
    if (!value) {
      return "";
    }
    if (value === "free") {
      return "free";
    }
    return value;
  }

  function escHtml(value) {
    const div = document.createElement("div");
    div.textContent = value || "";
    return div.innerHTML;
  }

  function proofUiConfig(method, address) {
    const normalizedMethod = (method || "bitcoin-message").toLowerCase();
    const normalizedAddress = String(address || "").trim().toLowerCase();
    if (normalizedMethod === "bip322-simple") {
      return {
        summary: "Sign this exact one-time challenge with a wallet that supports BIP-322 simple signing for your bc1q address.",
        intro: "Fund Registry will verify a BIP-322 simple signature for your native SegWit funding address.",
        label: "Paste your base64 BIP-322 signature:",
        placeholder: "Paste the base64 BIP-322 signature here",
        help: `
          <li><strong>Supported today:</strong> native SegWit <code style="font-size: 0.75rem; background: var(--surface); padding: 2px 6px; border-radius: 2px;">bc1q...</code> addresses via BIP-322 simple signatures.</li>
          <li><strong>Compatible wallets:</strong> use a wallet flow that explicitly mentions BIP-322 or Sign/Verify Message support for modern Bitcoin addresses.</li>
          <li><strong>Important:</strong> <code style="font-size: 0.75rem; background: var(--surface); padding: 2px 6px; border-radius: 2px;">bitcoin-cli signmessage</code> does not work for this proof path.</li>
        `,
      };
    }
    return {
      summary: "Sign this message with your Bitcoin wallet:",
      intro: normalizedAddress.startsWith("1")
        ? "Fund Registry will verify a legacy Bitcoin Signed Message for your 1-address."
        : "Fund Registry will verify a Bitcoin Signed Message for the listed funding address.",
      label: "Paste your base64 signature:",
      placeholder: "Paste the signature here",
      help: `
        <li><strong>Sparrow:</strong> Tools → Sign/Verify Message → paste message → sign with your address</li>
        <li><strong>Electrum:</strong> Tools → Sign/Verify Message → paste message → sign</li>
        <li><strong>bitcoin-cli:</strong> <code style="font-size: 0.75rem; background: var(--surface); padding: 2px 6px; border-radius: 2px;">bitcoin-cli signmessage "YOUR_ADDRESS" "MESSAGE"</code></li>
      `,
    };
  }

  function renderProofUi(method) {
    const config = proofUiConfig(method, currentPage?.btc_address);
    const summaryEl = document.getElementById("proofMethodSummary");
    const introEl = document.getElementById("proofIntro");
    const helpList = document.getElementById("proofHelpList");
    const labelEl = document.getElementById("proofSignatureLabel");
    const signatureEl = document.getElementById("proofSignature");
    if (summaryEl) {
      summaryEl.textContent = config.summary;
    }
    if (introEl) {
      introEl.textContent = config.intro;
    }
    if (helpList) {
      helpList.innerHTML = config.help;
    }
    if (labelEl) {
      labelEl.textContent = config.label;
    }
    if (signatureEl) {
      signatureEl.placeholder = config.placeholder;
    }
  }

  function formatDate(iso) {
    if (!iso) {
      return "—";
    }
    const date = new Date(iso);
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return `${months[date.getMonth()]} ${date.getDate()}, ${date.getFullYear()}`;
  }

  function formatDateTime(iso) {
    if (!iso) {
      return "Unknown";
    }
    return new Date(iso).toLocaleString([], {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  }

  function paymentStatusLabel(status) {
    switch (status) {
      case "confirming":
        return "Confirming";
      case "paid_pending_proof":
        return "Paid — proof required";
      case "paid":
        return "Paid";
      case "expired":
        return "Expired";
      default:
        return "Pending";
    }
  }

  function stopPaymentPolling() {
    if (paymentPollTimer) {
      window.clearTimeout(paymentPollTimer);
      paymentPollTimer = null;
    }
  }

  function shouldPollPayment(payment) {
    return Boolean(payment && ["pending", "confirming", "paid_pending_proof"].includes(payment.payment_status));
  }

  function schedulePaymentPolling() {
    stopPaymentPolling();
    if (!shouldPollPayment(currentPaymentIntent)) {
      return;
    }
    paymentPollTimer = window.setTimeout(refreshPaymentIntent, PAYMENT_POLL_INTERVAL_MS);
  }

  function setAuthError(message) {
    if (!authError || !authErrorText) {
      return;
    }
    authError.style.display = "flex";
    authErrorText.textContent = message;
  }

  function clearAuthError() {
    if (authError) {
      authError.style.display = "none";
    }
  }

  function loadKeyFile(file) {
    const reader = new FileReader();
    reader.onload = (event) => {
      if (keyInput) {
        keyInput.value = String(event.target?.result || "");
      }
      authenticate();
    };
    reader.readAsText(file);
  }

  function renderLinks(page) {
    const linksDiv = document.getElementById("linksContainer");
    if (!linksDiv) {
      return;
    }
    linksDiv.innerHTML = "";
    if (page.links && page.links.length) {
      page.links.forEach((link) => {
        const row = document.createElement("div");
        row.className = "link-row";
        const badge = document.createElement("span");
        badge.className = "badge";
        badge.style.minWidth = "70px";
        badge.style.justifyContent = "center";
        badge.textContent = link.platform || "Link";
        const anchor = document.createElement("a");
        anchor.href = link.url;
        anchor.target = "_blank";
        anchor.rel = "noopener noreferrer";
        anchor.style.fontSize = "0.875rem";
        anchor.style.overflow = "hidden";
        anchor.style.textOverflow = "ellipsis";
        anchor.textContent = link.url;
        row.appendChild(badge);
        row.appendChild(anchor);
        linksDiv.appendChild(row);
      });
      return;
    }
    linksDiv.innerHTML =
      '<div style="font-size:0.8125rem;color:var(--text-3);">No links added.</div>';
  }

  function hasPendingTierAction() {
    return Boolean(currentPaymentIntent && ["pending", "confirming", "paid_pending_proof"].includes(currentPaymentIntent.payment_status));
  }

  function renderUpgradeOptions(page) {
    if (!upgradeOptions) {
      return;
    }
    const btcPausedNotice = document.getElementById("btcPausedNotice");
    const paused = Boolean(page.payments_paused);
    const redacted = Boolean(page.payment_details_redacted);

    if (hasPendingTierAction()) {
      upgradeOptions.innerHTML =
        '<div style="font-size:0.875rem;color:var(--text-2);">A checkout request is already active. Finish or let it expire before starting another upgrade.</div>';
      if (btcPausedNotice) {
        btcPausedNotice.style.display = "none";
      }
      return;
    }
    if (page.tier === "tier3") {
      upgradeOptions.innerHTML =
        '<div style="font-size:0.875rem;color:var(--text-2);">You\'re on the highest tier.</div>';
      if (btcPausedNotice) {
        btcPausedNotice.style.display = "none";
      }
      return;
    }
    const buttons = [];
    if (page.tier === "free") {
      buttons.push(
        `<button class="btn btn-small btn-primary" data-upgrade-tier="tier2" style="margin-right:var(--space-sm);"${paused ? " disabled" : ""}>tier2 beta</button>`
      );
      buttons.push(
        `<button class="btn btn-small btn-secondary" data-upgrade-tier="tier3"${paused ? " disabled" : ""}>tier3 beta</button>`
      );
    } else if (page.tier === "tier2") {
      buttons.push(
        `<button class="btn btn-small btn-primary" data-upgrade-tier="tier3"${paused ? " disabled" : ""}>tier3 beta</button>`
      );
    }
    upgradeOptions.innerHTML = buttons.join("");
    if (btcPausedNotice) {
      if (paused) {
        btcPausedNotice.innerHTML = `
          <span style="display: inline-block; background: var(--warn-bg); color: var(--warn-text); font-size: 0.75rem; font-weight: 600; padding: 1px 8px; border-radius: 2px; margin-bottom: var(--space-xs);">Paused</span><br>
          Bitcoin checkout is temporarily paused. Use an invite code right now.
        `;
        btcPausedNotice.style.display = "block";
      } else if (redacted) {
        btcPausedNotice.innerHTML = `
          <span style="display: inline-block; background: var(--surface-elevated); color: var(--text-2); font-size: 0.75rem; font-weight: 600; padding: 1px 8px; border-radius: 2px; margin-bottom: var(--space-xs);">Invite-code test</span><br>
          Bitcoin checkout is ready, but payment details are hidden during invite-code testing. You can still use an invite code right now.
        `;
        btcPausedNotice.style.display = "block";
      } else {
        btcPausedNotice.style.display = "none";
      }
    }
  }

  function renderPaymentCard() {
    if (!paymentCard || !paymentCardBody) {
      return;
    }
    const payment = currentPaymentIntent;
    if (!payment || payment.payment_status === "paid") {
      paymentCard.style.display = "none";
      stopPaymentPolling();
      return;
    }

    const status = payment.payment_status || payment.status || "pending";
    const purposeLabel = payment.purpose === "renew"
      ? "Renewal"
      : payment.purpose === "upgrade"
        ? "Upgrade"
        : "Activation";
    const nextStep = status === "paid_pending_proof"
      ? "Payment is confirmed. Scroll down and complete wallet verification from the funding address listed on this page."
      : status === "confirming"
        ? "Payment detected. Fund Registry will unlock wallet proof after the first confirmation."
        : status === "expired"
          ? "This checkout request expired. Start a fresh upgrade or renewal when you're ready."
          : "Send the exact BTC amount below. Fund Registry will wait for 1 confirmation before the wallet proof step.";
    const receivedDetails = payment.received_sats > 0
      ? `<div>Received so far: <strong>${escHtml(String(payment.received_sats))}</strong> sats</div>`
      : "";
    const underpayDetails = payment.underpaid_sats > 0 && payment.received_sats > 0
      ? `<div style="color:#C62828;">Still short by ${escHtml(String(payment.underpaid_sats))} sats.</div>`
      : "";
    const overpayDetails = payment.overpaid_sats > 0
      ? `<div style="color:var(--accent);">Overpaid by ${escHtml(String(payment.overpaid_sats))} sats. Fund Registry will still accept the payment once confirmed.</div>`
      : "";
    const latePaymentDetails = payment.late_payment_detected
      ? `<div style="color:#C62828;">Funds arrived after expiry. This request will not auto-activate; create a fresh payment request.</div>`
      : "";
    const backendWarning = payment.payment_backend_error
      ? `<div class="warning" style="margin-top: var(--space-md);"><span class="warning-icon">⚠</span><div>${escHtml(payment.payment_backend_error)}</div></div>`
      : "";
    if (payment.payment_ui_redacted) {
      paymentCardBody.innerHTML = `
        <div class="badge badge-tier" style="margin-bottom: var(--space-sm);">Invite-code test</div>
        <div style="display:flex; gap: var(--space-lg); flex-wrap: wrap; align-items: flex-start;">
          <div aria-hidden="true" style="width: 180px; height: 180px; border-radius: var(--radius-sm); border: 1px solid var(--border); background: linear-gradient(135deg, rgba(44,44,44,0.14) 0%, rgba(44,44,44,0.03) 100%), repeating-linear-gradient(0deg, rgba(44,44,44,0.08) 0 9px, rgba(255,255,255,0.2) 9px 18px); filter: blur(1.8px);"></div>
          <div style="flex: 1; min-width: 260px;">
            <div style="font-size: 0.875rem; color: var(--text-2); margin-bottom: var(--space-sm);">${escHtml(purposeLabel)} to ${escHtml(tierLabel(payment.target_tier))}</div>
            <div style="font-size:1.25rem;font-weight:600;margin-bottom:var(--space-xs);">${escHtml(String(payment.amount_sats))} sats</div>
            <div style="font-size:0.875rem;color:var(--text-2);margin-bottom:var(--space-md);">${escHtml(payment.amount_btc)} BTC</div>
            <div style="font-size:0.8125rem;color:var(--text-2);line-height:1.8;">
              <div>${escHtml(payment.payment_ui_message || "Bitcoin checkout is available, but payment details are hidden.")}</div>
              <div>Use an invite code now, or wait until BTC details are opened.</div>
              <div>Expires: <strong>${escHtml(formatDateTime(payment.expires_at))}</strong></div>
              ${receivedDetails}
              ${underpayDetails}
              ${overpayDetails}
              ${latePaymentDetails}
            </div>
          </div>
        </div>
        ${backendWarning}
      `;
      paymentCard.style.display = "block";
      schedulePaymentPolling();
      return;
    }
    if (payment.payment_ui_paused) {
      paymentCardBody.innerHTML = `
        <div class="badge badge-tier" style="margin-bottom: var(--space-sm);">Paused</div>
        <div style="display:flex; gap: var(--space-lg); flex-wrap: wrap; align-items: flex-start;">
          <div aria-hidden="true" style="width: 180px; height: 180px; border-radius: var(--radius-sm); border: 1px solid var(--border); background: linear-gradient(135deg, rgba(44,44,44,0.14) 0%, rgba(44,44,44,0.03) 100%), repeating-linear-gradient(0deg, rgba(44,44,44,0.08) 0 9px, rgba(255,255,255,0.2) 9px 18px); filter: blur(1.8px);"></div>
          <div style="flex: 1; min-width: 260px;">
            <div style="font-size: 0.875rem; color: var(--text-2); margin-bottom: var(--space-sm);">${escHtml(purposeLabel)} to ${escHtml(tierLabel(payment.target_tier))}</div>
            <div style="font-size:1.25rem;font-weight:600;margin-bottom:var(--space-xs);">${escHtml(String(payment.amount_sats))} sats</div>
            <div style="font-size:0.875rem;color:var(--text-2);margin-bottom:var(--space-md);">${escHtml(payment.amount_btc)} BTC</div>
            <div style="font-size:0.8125rem;color:var(--text-2);line-height:1.8;">
              <div>${escHtml(payment.payment_ui_message || "Bitcoin checkout is temporarily paused.")}</div>
              <div>Payment details are hidden until the updated payment UI is ready.</div>
              <div>Expires: <strong>${escHtml(formatDateTime(payment.expires_at))}</strong></div>
              ${receivedDetails}
              ${underpayDetails}
              ${overpayDetails}
              ${latePaymentDetails}
            </div>
          </div>
        </div>
        ${backendWarning}
      `;
      paymentCard.style.display = "block";
      stopPaymentPolling();
      return;
    }

    paymentCardBody.innerHTML = `
      <div class="badge badge-tier" style="margin-bottom: var(--space-sm);">${escHtml(paymentStatusLabel(status))}</div>
      <div style="font-size: 0.875rem; color: var(--text-2); margin-bottom: var(--space-sm);">${escHtml(purposeLabel)} to ${escHtml(tierLabel(payment.target_tier))}</div>
      <div style="display:flex; gap: var(--space-lg); flex-wrap: wrap; align-items: flex-start;">
        <div style="min-width: 180px;">
          <img src="${escHtml(payment.qr_image_uri || "")}" alt="Bitcoin checkout QR code" style="width: 180px; height: 180px; border-radius: var(--radius-sm); border: 1px solid var(--border); background: white;">
        </div>
        <div style="flex: 1; min-width: 260px;">
          <div style="font-size:1.25rem;font-weight:600;margin-bottom:var(--space-xs);">${escHtml(String(payment.amount_sats))} sats</div>
          <div style="font-size:0.875rem;color:var(--text-2);margin-bottom:var(--space-md);">${escHtml(payment.amount_btc)} BTC</div>
          <div class="form-hint" style="margin-bottom: var(--space-xs);">Receive address</div>
          <div class="key-display mono" style="font-size:0.8125rem;margin-bottom:var(--space-sm);">${escHtml(payment.payment_address || "")}</div>
          <div style="display:flex; gap: var(--space-sm); flex-wrap: wrap; margin-bottom: var(--space-md);">
            <button class="btn btn-small btn-secondary" data-copy-value="${escHtml(payment.payment_address || "")}">Copy address</button>
            <button class="btn btn-small btn-secondary" data-copy-value="${escHtml(payment.payment_uri || "")}">Copy bitcoin: URI</button>
          </div>
          <div style="font-size:0.8125rem;color:var(--text-2);line-height:1.8;">
            <div>Expires: <strong>${escHtml(formatDateTime(payment.expires_at))}</strong></div>
            <div>Confirmations required: <strong>${escHtml(String(payment.confirmation_target || 1))}</strong></div>
            <div>Next step: ${escHtml(nextStep)}</div>
            ${receivedDetails}
            ${underpayDetails}
            ${overpayDetails}
            ${latePaymentDetails}
          </div>
        </div>
      </div>
      ${backendWarning}
    `;
    paymentCard.style.display = "block";
    schedulePaymentPolling();
  }

  function renderProofCard(page) {
    const proofCard = document.getElementById("proofCard");
    if (!proofCard) {
      return;
    }

    const isVerified =
      page.wallet_proof_verified_at ||
      page.proof_status === "verified" ||
      page.proof_status === "anchored";
    const paymentReadyForProof =
      currentPaymentIntent && currentPaymentIntent.payment_status === "paid_pending_proof";
    const isPaid = page.tier === "tier2" || page.tier === "tier3";
    const proofIntro = document.getElementById("proofIntro");

    if (isVerified) {
      proofCard.style.display = "block";
      document.getElementById("proofStep1").style.display = "none";
      document.getElementById("proofStep2").style.display = "none";
      document.getElementById("proofStep3").style.display = "block";
      if (proofIntro) {
        proofIntro.textContent = "Wallet verification complete.";
      }
      return;
    }

    if (!isPaid && !paymentReadyForProof) {
      proofCard.style.display = "none";
      return;
    }

    proofCard.style.display = "block";
    document.getElementById("proofStep1").style.display = "block";
    document.getElementById("proofStep2").style.display = "none";
    document.getElementById("proofStep3").style.display = "none";
    if (proofIntro) {
      proofIntro.textContent = paymentReadyForProof
        ? "Payment is confirmed. Prove you control the Bitcoin address on this page to finish activation."
        : "Prove you control the Bitcoin address on this page. This requires signing a message with your wallet.";
    }
  }

  function renderDashboard(page) {
    if (!page) {
      return;
    }
    document.title = `${page.title} — Manage — Fund Registry`;
    document.getElementById("pageTitle").textContent = page.title;
    document.getElementById("viewPublicLink").href = page.canonical_url || `/fund/${page.page_ref}`;

    let meta = page.proof_status === "anchored"
      ? '<span class="badge badge-tier">⬢ Bitcoin Anchored</span>'
      : page.wallet_proof_verified_at
        ? '<span class="badge badge-verified">● Wallet Verified</span>'
        : '<span class="badge badge-unverified">○ Unverified</span>';
    meta += '<span class="meta-separator">·</span>';
    meta += `<span class="badge badge-tier">${escHtml(tierLabel(page.tier))}</span>`;
    if (page.requested_tier) {
      meta += '<span class="meta-separator">·</span>';
      meta += `<span class="meta-item">Requested ${escHtml(tierLabel(page.requested_tier))}</span>`;
    }
    if (page.verification_code) {
      meta += '<span class="meta-separator">·</span>';
      meta += `<span class="meta-item mono">${escHtml(page.verification_code)}</span>`;
    }
    document.getElementById("pageMeta").innerHTML = meta;

    document.getElementById("statTier").textContent = tierLabel(page.tier);
    document.getElementById("statStatus").textContent = capitalize(page.public_state);
    document.getElementById("statExpires").textContent = page.active_until ? formatDate(page.active_until) : "—";
    document.getElementById("statContributions").textContent = page.contribution_count || "0";

    const photoDiv = document.getElementById("currentPhoto");
    if (page.story_photo_url) {
      photoDiv.innerHTML = `<img src="${escHtml(page.story_photo_url)}" class="photo-preview" alt="Campaign photo">`;
    } else {
      photoDiv.innerHTML =
        '<div style="font-size:0.8125rem;color:var(--text-3);margin-bottom:var(--space-sm);">No photo uploaded yet.</div>';
    }

    renderLinks(page);
    renderUpgradeOptions(page);
    renderPaymentCard();
    renderProofCard(page);
    if (renewBtn) {
      renewBtn.disabled = page.tier === "free" || hasPendingTierAction();
    }
    document.getElementById("renewCard").style.display = page.tier === "free" ? "none" : "block";
    const upgradeCard = document.getElementById("upgradeCard");
    if (upgradeCard) {
      upgradeCard.style.display = page.tier === "tier3" ? "none" : "block";
    }
  }

  function setPageState(page, paymentIntent) {
    currentPage = page;
    if (paymentIntent !== undefined) {
      currentPaymentIntent = paymentIntent;
    } else if (page && Object.prototype.hasOwnProperty.call(page, "payment_intent")) {
      currentPaymentIntent = page.payment_intent || null;
    }
    renderDashboard(currentPage);
  }

  function authenticate() {
    const raw = keyInput?.value.trim() || "";
    if (!raw) {
      setAuthError("Please paste your Campaign Key JSON or upload the key file.");
      return;
    }
    try {
      let parsed = JSON.parse(raw);
      if (parsed.campaign_key) {
        parsed = parsed.campaign_key;
      }
      if (!parsed.page_id || !parsed.secret) {
        throw new Error("Missing required fields");
      }
      campaignKey = parsed;
    } catch (_error) {
      setAuthError("Invalid Campaign Key JSON. Make sure you paste the complete key file contents.");
      return;
    }

    clearAuthError();
    if (authBtn) {
      authBtn.textContent = "Unlocking...";
    }

    api("POST", "/v1/pages/manage", { campaign_key: campaignKey })
      .then((page) => {
        setPageState(page, page.payment_intent || null);
        authScreen?.classList.add("hidden");
        dashboard?.classList.add("active");
        if (authBtn) {
          authBtn.textContent = "Unlock page";
        }
      })
      .catch((error) => {
        let msg = error.message;
        if (error.status === 403 || /invalid|does not match|revoked/i.test(msg)) {
          msg += " If you have lost your key, you can recover access by signing a challenge with your funding wallet.";
        }
        setAuthError(msg);
        if (authBtn) {
          authBtn.textContent = "Unlock page";
        }
      });
  }

  function refreshPaymentIntent() {
    if (!currentPaymentIntent?.id) {
      stopPaymentPolling();
      return;
    }
    api("GET", `/v1/payments/${currentPaymentIntent.id}`)
      .then((payment) => {
        const previousStatus = currentPaymentIntent?.payment_status;
        currentPaymentIntent = payment;
        renderPaymentCard();
        renderProofCard(currentPage);
        renderUpgradeOptions(currentPage);
        if (payment.payment_status === "paid_pending_proof" && previousStatus !== "paid_pending_proof") {
          showToast("Payment confirmed. Finish wallet verification below.");
        }
      })
      .catch((_error) => {
        schedulePaymentPolling();
      });
  }

  function postUpdate() {
    const body = document.getElementById("updateBody")?.value.trim() || "";
    if (!body || !currentPage) {
      showToast("Write something first.", true);
      return;
    }
    api("POST", `/v1/pages/${currentPage.id}/updates`, { campaign_key: campaignKey, body })
      .then((data) => {
        document.getElementById("updateBody").value = "";
        setPageState(data.page);
        showToast("Update posted.");
      })
      .catch((error) => showToast(error.message, true));
  }

  function uploadPhoto(file) {
    if (!currentPage) {
      return;
    }
    if (file.size > 200 * 1024) {
      showToast("Photo must be under 200KB.", true);
      return;
    }
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
      showToast("Only JPEG, PNG, or WebP.", true);
      return;
    }
    const reader = new FileReader();
    reader.onload = (event) => {
      const payload = String(event.target?.result || "").split(",")[1];
      const photoPath = currentPage?.tier === "tier3" ? "progress-photo" : "photo";
      api("POST", `/v1/pages/${currentPage.id}/${photoPath}`, {
        campaign_key: campaignKey,
        content_type: file.type,
        image_base64: payload,
      })
        .then((data) => {
          setPageState(data.page);
          showToast(currentPage?.tier === "tier3" ? "Progress photo uploaded." : "Photo uploaded.");
        })
        .catch((error) => showToast(error.message, true));
    };
    reader.readAsDataURL(file);
  }

  function applyPromo() {
    const code = document.getElementById("promoCode")?.value.trim() || "";
    const promoResult = document.getElementById("promoResult");
    if (!code || !currentPage) {
      return;
    }

    api("POST", "/v1/promo/validate", { campaign_key: campaignKey, code })
      .then((data) => {
        if (!data.valid) {
          throw new Error(data.detail || data.reason || "Invalid invite code");
        }
        const targetTier = data.eligible_tiers && data.eligible_tiers[0];
        if (!targetTier) {
          throw new Error("No eligible tier for this invite code.");
        }
        return api("POST", `/v1/pages/${currentPage.id}/promo/apply`, {
          campaign_key: campaignKey,
          code,
          target_tier: targetTier,
        });
      })
      .then((data) => {
        if (!data?.page) {
          return;
        }
        if (promoResult) {
          promoResult.textContent = "";
        }
        document.getElementById("promoCode").value = "";
        currentPaymentIntent = null;
        setPageState(data.page, null);
        showToast("Invite code applied. Tier unlocked.");
      })
      .catch((error) => {
        if (promoResult) {
          promoResult.innerHTML = `<span style="color:#C62828;">${escHtml(error.message)}</span>`;
        }
      });
  }

  function upgradeTier(targetTier) {
    if (!currentPage) {
      return;
    }
    api("POST", `/v1/pages/${currentPage.id}/upgrade`, {
      campaign_key: campaignKey,
      target_tier: targetTier,
    })
      .then((data) => {
        setPageState(data.page, data.payment_intent || null);
        if (data.payment_intent) {
          showToast(`Payment request created for ${data.payment_intent.amount_sats} sats.`);
        }
      })
      .catch((error) => showToast(error.message, true));
  }

  function renewPage() {
    if (!currentPage) {
      return;
    }
    api("POST", `/v1/pages/${currentPage.id}/renew`, { campaign_key: campaignKey })
      .then((data) => {
        setPageState(data.page, data.payment_intent || null);
        if (data.payment_intent) {
          showToast(`Renewal payment request created for ${data.payment_intent.amount_sats} sats.`);
        }
      })
      .catch((error) => showToast(error.message, true));
  }

  function startProof() {
    if (!currentPage) {
      return;
    }
    const btn = document.getElementById("startProofBtn");
    if (btn) {
      btn.textContent = "Preparing...";
    }

    api("POST", `/v1/pages/${currentPage.id}/proof/prepare`, { campaign_key: campaignKey })
      .then((data) => {
        currentChallengeId = data.challenge?.id;
        renderProofUi(data.challenge?.proof_method || "bitcoin-message");
        const messageEl = document.getElementById("proofMessage");
        if (messageEl) {
          messageEl.value = data.payload_json || data.challenge?.challenge_text || "";
        }
        const signatureEl = document.getElementById("proofSignature");
        if (signatureEl) {
          signatureEl.value = "";
        }
        document.getElementById("proofStep1").style.display = "none";
        document.getElementById("proofStep2").style.display = "block";
        if (btn) {
          btn.textContent = "Start verification";
        }
      })
      .catch((error) => {
        showToast(error.message, true);
        if (btn) {
          btn.textContent = "Start verification";
        }
      });
  }

  function submitProof() {
    if (!currentPage || !currentChallengeId) {
      return;
    }
    const sig = document.getElementById("proofSignature")?.value.trim() || "";
    const errorEl = document.getElementById("proofError");
    if (!sig) {
      if (errorEl) {
        errorEl.style.display = "block";
        errorEl.textContent = "Paste your signature first.";
      }
      return;
    }
    const btn = document.getElementById("submitProofBtn");
    if (btn) {
      btn.textContent = "Verifying...";
    }
    if (errorEl) {
      errorEl.style.display = "none";
    }

    api("POST", `/v1/pages/${currentPage.id}/proof/verify`, {
      campaign_key: campaignKey,
      challenge_id: currentChallengeId,
      proof: sig,
    })
      .then((data) => {
        if (data.page) {
          setPageState(data.page, data.payment_intent || null);
        }
        document.getElementById("proofStep2").style.display = "none";
        document.getElementById("proofStep3").style.display = "block";
        document.getElementById("proofIntro").textContent = "Wallet verification complete.";
        showToast("Wallet verified!");
        if (btn) {
          btn.textContent = "Submit proof";
        }
      })
      .catch((error) => {
        if (errorEl) {
          errorEl.style.display = "block";
          errorEl.textContent = error.message;
        }
        if (btn) {
          btn.textContent = "Submit proof";
        }
      });
  }

  let abortConfirmPending = false;
  let abortConfirmTimer = null;

  function archivePage() {
    if (!currentPage) {
      return;
    }
    const btn = document.getElementById("archivePageBtn");
    if (!abortConfirmPending) {
      abortConfirmPending = true;
      if (btn) {
        btn.textContent = "Confirm abort — this is permanent";
        btn.style.outline = "2px solid var(--danger, #e53e3e)";
      }
      abortConfirmTimer = setTimeout(() => {
        abortConfirmPending = false;
        if (btn) {
          btn.textContent = "Abort this campaign";
          btn.style.outline = "";
        }
      }, 6000);
      return;
    }
    clearTimeout(abortConfirmTimer);
    abortConfirmPending = false;
    if (btn) {
      btn.textContent = "Aborting...";
      btn.disabled = true;
    }
    api("POST", `/v1/pages/${currentPage.id}/abort`, { campaign_key: campaignKey })
      .then((page) => {
        currentPaymentIntent = null;
        setPageState(page, null);
        showToast("Campaign aborted.");
      })
      .catch((error) => {
        showToast(error.message, true);
        if (btn) {
          btn.textContent = "Abort this campaign";
          btn.style.outline = "";
          btn.disabled = false;
        }
      });
  }

  function addLinkRow() {
    showToast("Links can only be corrected during the first 24 hours after page creation.", true);
  }

  fileDrop?.addEventListener("click", () => fileInput?.click());
  fileDrop?.addEventListener("dragover", (event) => {
    event.preventDefault();
    fileDrop.classList.add("dragover");
  });
  fileDrop?.addEventListener("dragleave", () => {
    fileDrop.classList.remove("dragover");
  });
  fileDrop?.addEventListener("drop", (event) => {
    event.preventDefault();
    fileDrop.classList.remove("dragover");
    const file = event.dataTransfer?.files?.[0];
    if (file) {
      loadKeyFile(file);
    }
  });
  fileInput?.addEventListener("change", () => {
    const file = fileInput.files?.[0];
    if (file) {
      loadKeyFile(file);
    }
  });

  photoDrop?.addEventListener("click", () => photoInput?.click());
  photoDrop?.addEventListener("dragover", (event) => {
    event.preventDefault();
    photoDrop.classList.add("dragover");
  });
  photoDrop?.addEventListener("dragleave", () => {
    photoDrop.classList.remove("dragover");
  });
  photoDrop?.addEventListener("drop", (event) => {
    event.preventDefault();
    photoDrop.classList.remove("dragover");
    const file = event.dataTransfer?.files?.[0];
    if (file) {
      uploadPhoto(file);
    }
  });
  photoInput?.addEventListener("change", () => {
    const file = photoInput.files?.[0];
    if (file) {
      uploadPhoto(file);
    }
  });

  authBtn?.addEventListener("click", authenticate);
  document.getElementById("startProofBtn")?.addEventListener("click", startProof);
  document.getElementById("submitProofBtn")?.addEventListener("click", submitProof);
  document.getElementById("copyProofMsg")?.addEventListener("click", () => {
    const msg = document.getElementById("proofMessage")?.value || "";
    navigator.clipboard.writeText(msg).then(() => showToast("Message copied.")).catch(() => {});
  });
  dashboard?.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    const copyValue = target.getAttribute("data-copy-value");
    if (copyValue) {
      navigator.clipboard.writeText(copyValue).then(() => showToast("Copied.")).catch(() => {});
      return;
    }
    const tier = target.getAttribute("data-upgrade-tier");
    if (tier) {
      upgradeTier(tier);
    }
  });
  postUpdateBtn?.addEventListener("click", postUpdate);
  addLinkBtn?.addEventListener("click", addLinkRow);
  applyPromoBtn?.addEventListener("click", applyPromo);
  renewBtn?.addEventListener("click", renewPage);
  archivePageBtn?.addEventListener("click", archivePage);
});
