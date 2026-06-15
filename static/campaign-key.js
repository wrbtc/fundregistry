document.addEventListener("DOMContentLoaded", () => {
  const CREATE_RESULT_KEY = "fundRegistryCreateResultV1";
  const POLL_INTERVAL_MS = 10000;

  const step1 = document.getElementById("step1");
  const step2 = document.getElementById("step2");
  const step3 = document.getElementById("step3");
  const ack1 = document.getElementById("ack1");
  const ack2 = document.getElementById("ack2");
  const generateBtn = document.getElementById("generateBtn");
  const doneBtn = document.getElementById("doneBtn");
  const copyKeyBtn = document.getElementById("copyKeyBtn");
  const downloadKeyBtn = document.getElementById("downloadKeyBtn");
  const keyValue = document.getElementById("keyValue");
  const completionMessage = document.getElementById("completionMessage");
  const paymentRequestCard = document.getElementById("paymentRequestCard");
  const inviteCodeCard = document.getElementById("inviteCodeCard");
  const inviteCodeInput = document.getElementById("inviteCodeInput");
  const inviteCodeResult = document.getElementById("inviteCodeResult");
  const applyInviteCodeBtn = document.getElementById("applyInviteCodeBtn");
  const viewPageBtn = document.getElementById("viewPageBtn");
  const managePageBtn = document.getElementById("managePageBtn");

  let createResult = null;
  let currentPaymentIntent = null;
  let pollTimer = null;

  function warnBeforeLeave(event) {
    event.preventDefault();
    event.returnValue = "";
  }

  function api(method, path, body) {
    return fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    }).then(async (response) => {
      let data = {};
      try {
        data = await response.json();
      } catch (_error) {
        data = {};
      }
      if (!response.ok) {
        throw new Error(data.detail || "Request failed");
      }
      return data;
    });
  }

  function escHtml(value) {
    const div = document.createElement("div");
    div.textContent = value || "";
    return div.innerHTML;
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

  function stopPolling() {
    if (pollTimer) {
      window.clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  function shouldPoll(payment) {
    return Boolean(payment && ["pending", "confirming", "paid_pending_proof"].includes(payment.payment_status));
  }

  function schedulePoll() {
    stopPolling();
    if (!shouldPoll(currentPaymentIntent)) {
      return;
    }
    pollTimer = window.setTimeout(refreshPayment, POLL_INTERVAL_MS);
  }

  function updateCompletionMessage() {
    if (!completionMessage || !createResult?.page) {
      return;
    }
    if (!currentPaymentIntent) {
      completionMessage.textContent = "✓ Your funding page is live. Campaign Key saved.";
      return;
    }
    if (currentPaymentIntent.payment_ui_redacted) {
      completionMessage.textContent =
        "✓ Your page is reserved. Bitcoin checkout is ready, but payment details are hidden during invite-code testing.";
      return;
    }
    if (currentPaymentIntent.payment_ui_paused) {
      completionMessage.textContent =
        "✓ Your page is reserved. Bitcoin checkout is temporarily paused while Fund Registry UI updates are in progress.";
      return;
    }
    if (currentPaymentIntent.payment_status === "paid_pending_proof") {
      completionMessage.textContent =
        "✓ Payment confirmed. Use your Campaign Key on the manage page to complete wallet verification.";
      return;
    }
    if (currentPaymentIntent.payment_status === "paid") {
      completionMessage.textContent = "✓ Payment and wallet verification complete. Your upgraded page is live.";
      return;
    }
    completionMessage.textContent =
      "✓ Your page is reserved. Save the Campaign Key, then complete the Bitcoin payment below to continue.";
  }

  function renderPaymentCard(payment) {
    if (!paymentRequestCard) {
      return;
    }
    if (!payment || (!payment.payment_uri && !payment.payment_ui_paused && !payment.payment_ui_redacted)) {
      paymentRequestCard.style.display = "none";
      stopPolling();
      updateCompletionMessage();
      return;
    }

    const status = payment.payment_status || payment.status || "pending";
    const nextStep = status === "paid_pending_proof"
      ? "Payment is confirmed. Open Manage and complete the wallet proof from the listed funding address."
      : status === "confirming"
        ? "Payment detected in the mempool. Fund Registry will advance after the first confirmation."
        : status === "expired"
          ? "This checkout window expired. Use the manage page to request a fresh payment."
          : "Send the exact BTC amount below. Fund Registry will wait for 1 confirmation before wallet proof.";

    const receivedLine = payment.received_sats > 0
      ? `<div style="margin-top: var(--space-xs);">Received: <strong>${escHtml(String(payment.received_sats))}</strong> sats</div>`
      : "";
    const underpayLine = payment.underpaid_sats > 0 && payment.received_sats > 0
      ? `<div style="margin-top: var(--space-xs); color: #C62828;">Still short by ${escHtml(String(payment.underpaid_sats))} sats.</div>`
      : "";
    const overpayLine = payment.overpaid_sats > 0
      ? `<div style="margin-top: var(--space-xs); color: var(--accent);">Overpaid by ${escHtml(String(payment.overpaid_sats))} sats. We will still accept the payment once it confirms.</div>`
      : "";
    const backendWarning = payment.payment_backend_error
      ? `<div class="warning" style="margin-top: var(--space-md);"><span class="warning-icon">⚠</span><div>${escHtml(payment.payment_backend_error)}</div></div>`
      : "";
    if (payment.payment_ui_redacted) {
      paymentRequestCard.innerHTML = `
        <div class="section-title">Pay with BTC</div>
        <div style="display:flex; gap: var(--space-lg); flex-wrap: wrap; align-items: flex-start;">
          <div aria-hidden="true" style="width: 180px; height: 180px; border-radius: var(--radius-sm); border: 1px solid var(--border); background: linear-gradient(135deg, rgba(44,44,44,0.14) 0%, rgba(44,44,44,0.03) 100%), repeating-linear-gradient(0deg, rgba(44,44,44,0.08) 0 9px, rgba(255,255,255,0.2) 9px 18px); filter: blur(1.8px);"></div>
          <div style="flex: 1; min-width: 260px;">
            <div class="badge badge-tier" style="margin-bottom: var(--space-sm);">Invite-code test</div>
            <div style="font-size: 1.25rem; font-weight: 600; margin-bottom: var(--space-xs);">${escHtml(String(payment.amount_sats))} sats</div>
            <div style="font-size: 0.875rem; color: var(--text-2); margin-bottom: var(--space-md);">${escHtml(payment.amount_btc)} BTC</div>
            <div style="font-size: 0.875rem; color: var(--text-2); line-height: 1.8;">
              <div>${escHtml(payment.payment_ui_message || "Bitcoin checkout is available, but payment details are hidden.")}</div>
              <div>Use an invite code right now, or wait until BTC details are opened.</div>
              <div>Expires: <strong>${escHtml(formatDateTime(payment.expires_at))}</strong></div>
            </div>
          </div>
        </div>
        ${backendWarning}
      `;
      paymentRequestCard.style.display = "block";
      updateCompletionMessage();
      schedulePoll();
      return;
    }
    if (payment.payment_ui_paused) {
      paymentRequestCard.innerHTML = `
        <div class="section-title">Bitcoin checkout paused</div>
        <div style="display:flex; gap: var(--space-lg); flex-wrap: wrap; align-items: flex-start;">
          <div aria-hidden="true" style="width: 180px; height: 180px; border-radius: var(--radius-sm); border: 1px solid var(--border); background: linear-gradient(135deg, rgba(44,44,44,0.14) 0%, rgba(44,44,44,0.03) 100%), repeating-linear-gradient(0deg, rgba(44,44,44,0.08) 0 9px, rgba(255,255,255,0.2) 9px 18px); filter: blur(1.8px);"></div>
          <div style="flex: 1; min-width: 260px;">
            <div class="badge badge-tier" style="margin-bottom: var(--space-sm);">Paused</div>
            <div style="font-size: 1.25rem; font-weight: 600; margin-bottom: var(--space-xs);">${escHtml(String(payment.amount_sats))} sats</div>
            <div style="font-size: 0.875rem; color: var(--text-2); margin-bottom: var(--space-md);">${escHtml(payment.amount_btc)} BTC</div>
            <div style="font-size: 0.875rem; color: var(--text-2); line-height: 1.8;">
              <div>${escHtml(payment.payment_ui_message || "Bitcoin checkout is temporarily paused.")}</div>
              <div>Save your Campaign Key now and come back when checkout reopens.</div>
              <div>Expires: <strong>${escHtml(formatDateTime(payment.expires_at))}</strong></div>
            </div>
          </div>
        </div>
        ${backendWarning}
      `;
      paymentRequestCard.style.display = "block";
      stopPolling();
      updateCompletionMessage();
      return;
    }

    paymentRequestCard.innerHTML = `
      <div class="section-title">Bitcoin checkout</div>
      <div style="display:flex; gap: var(--space-lg); flex-wrap: wrap; align-items: flex-start;">
        <div style="min-width: 180px;">
          <img src="${escHtml(payment.qr_image_uri || "")}" alt="Bitcoin payment QR code" style="width: 180px; height: 180px; border-radius: var(--radius-sm); border: 1px solid var(--border); background: white;">
        </div>
        <div style="flex: 1; min-width: 260px;">
          <div class="badge badge-tier" style="margin-bottom: var(--space-sm);">${escHtml(paymentStatusLabel(status))}</div>
          <div style="font-size: 1.25rem; font-weight: 600; margin-bottom: var(--space-xs);">${escHtml(String(payment.amount_sats))} sats</div>
          <div style="font-size: 0.875rem; color: var(--text-2); margin-bottom: var(--space-md);">${escHtml(payment.amount_btc)} BTC</div>
          <div class="form-hint" style="margin-bottom: var(--space-xs);">Receive address</div>
          <div class="key-display mono" style="font-size: 0.8125rem; margin-bottom: var(--space-sm);">${escHtml(payment.payment_address || "")}</div>
          <div style="display:flex; gap: var(--space-sm); flex-wrap: wrap; margin-bottom: var(--space-md);">
            <button class="btn btn-small btn-secondary" data-copy-value="${escHtml(payment.payment_address || "")}">Copy address</button>
            <button class="btn btn-small btn-secondary" data-copy-value="${escHtml(payment.payment_uri || "")}">Copy bitcoin: URI</button>
          </div>
          <div style="font-size: 0.8125rem; color: var(--text-2);">
            <div>Expires: <strong>${escHtml(formatDateTime(payment.expires_at))}</strong></div>
            <div>Confirmations required: <strong>${escHtml(String(payment.confirmation_target || 1))}</strong></div>
            <div>Next step: ${escHtml(nextStep)}</div>
            ${receivedLine}
            ${underpayLine}
            ${overpayLine}
          </div>
        </div>
      </div>
      ${backendWarning}
    `;
    paymentRequestCard.style.display = "block";
    updateCompletionMessage();
    schedulePoll();
  }

  function refreshPayment() {
    if (!currentPaymentIntent?.id) {
      return;
    }
    api("GET", `/v1/payments/${currentPaymentIntent.id}`)
      .then((payment) => {
        currentPaymentIntent = payment;
        renderPaymentCard(payment);
      })
      .catch((_error) => {
        schedulePoll();
      });
  }

  function loadCreateResult() {
    const raw = window.sessionStorage.getItem(CREATE_RESULT_KEY);
    if (!raw) {
      return null;
    }
    try {
      return JSON.parse(raw);
    } catch (_error) {
      return null;
    }
  }

  function syncViewLinks() {
    if (!createResult?.page) {
      return;
    }
    if (keyValue) {
      keyValue.value = JSON.stringify(createResult.campaign_key, null, 2);
    }
    if (viewPageBtn) {
      viewPageBtn.href = createResult.page.canonical_url || `/fund/${createResult.page.page_ref}`;
    }
    if (managePageBtn) {
      managePageBtn.href = "/manage";
    }
    currentPaymentIntent = createResult.payment_intent || null;
    renderPaymentCard(currentPaymentIntent);
    if (inviteCodeCard) {
      inviteCodeCard.style.display = currentPaymentIntent ? "block" : "none";
    }
  }

  function targetTierForInviteCode() {
    return currentPaymentIntent?.target_tier || createResult?.page?.requested_tier || null;
  }

  function applyInviteCode() {
    const code = inviteCodeInput?.value.trim() || "";
    const targetTier = targetTierForInviteCode();
    if (!code || !createResult?.page?.id || !createResult?.campaign_key || !targetTier) {
      return;
    }
    if (applyInviteCodeBtn) {
      applyInviteCodeBtn.disabled = true;
      applyInviteCodeBtn.textContent = "Applying...";
    }
    if (inviteCodeResult) {
      inviteCodeResult.textContent = "";
    }
    api("POST", "/v1/promo/validate", {
      campaign_key: createResult.campaign_key,
      code,
      target_tier: targetTier,
    })
      .then((data) => {
        if (!data.valid) {
          throw new Error(data.detail || data.reason || "Invalid invite code");
        }
        return api("POST", `/v1/pages/${createResult.page.id}/promo/apply`, {
          campaign_key: createResult.campaign_key,
          code,
          target_tier: targetTier,
        });
      })
      .then((data) => {
        if (!data?.page) {
          return;
        }
        createResult.page = data.page;
        createResult.payment_intent = null;
        window.sessionStorage.setItem(CREATE_RESULT_KEY, JSON.stringify(createResult));
        currentPaymentIntent = null;
        if (inviteCodeInput) {
          inviteCodeInput.value = "";
        }
        if (inviteCodeResult) {
          inviteCodeResult.innerHTML = '<span style="color: var(--accent);">Invite code applied. Your page is upgraded.</span>';
        }
        renderPaymentCard(null);
        updateCompletionMessage();
      })
      .catch((error) => {
        if (inviteCodeResult) {
          inviteCodeResult.innerHTML = `<span style="color:#C62828;">${escHtml(error.message)}</span>`;
        }
      })
      .finally(() => {
        if (applyInviteCodeBtn) {
          applyInviteCodeBtn.disabled = false;
          applyInviteCodeBtn.textContent = "Apply";
        }
      });
  }

  function showStep2() {
    if (!step1 || !step2) {
      return;
    }
    step1.style.display = "none";
    step2.style.display = "block";
    window.addEventListener("beforeunload", warnBeforeLeave);
  }

  function showStep3() {
    if (!step2 || !step3) {
      return;
    }
    window.removeEventListener("beforeunload", warnBeforeLeave);
    step2.style.display = "none";
    step3.style.display = "block";
    syncViewLinks();
  }

  function copyKey() {
    const value = keyValue?.value || "";
    if (!value || !copyKeyBtn) {
      return;
    }
    navigator.clipboard.writeText(value).then(() => {
      const original = copyKeyBtn.textContent;
      copyKeyBtn.textContent = "✓ Copied";
      window.setTimeout(() => {
        copyKeyBtn.textContent = original || "Copy";
      }, 2000);
    });
  }

  function downloadKey() {
    if (!createResult?.campaign_key) {
      return;
    }
    const blob = new Blob([JSON.stringify(createResult.campaign_key, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "campaign-key.json";
    link.click();
    URL.revokeObjectURL(url);
  }

  createResult = loadCreateResult();
  if (!createResult?.campaign_key || !createResult?.page) {
    if (step1) {
      step1.innerHTML = `
        <h1 style="margin-bottom: var(--space-lg);">Campaign Key unavailable</h1>
        <div class="warning">
          <span class="warning-icon">⚠</span>
          <div>Create a page first, then return here from the Fund Registry create flow.</div>
        </div>
        <div style="margin-top: var(--space-lg);">
          <a href="/create" class="btn btn-primary">Back to create</a>
        </div>
      `;
    }
    if (step2) {
      step2.style.display = "none";
    }
    if (step3) {
      step3.style.display = "none";
    }
    return;
  }

  if (keyValue) {
    keyValue.value = JSON.stringify(createResult.campaign_key, null, 2);
  }

  ack1?.addEventListener("change", () => {
    if (generateBtn) {
      generateBtn.disabled = !ack1.checked;
    }
  });
  ack2?.addEventListener("change", () => {
    if (doneBtn) {
      doneBtn.disabled = !ack2.checked;
    }
  });
  generateBtn?.addEventListener("click", showStep2);
  doneBtn?.addEventListener("click", showStep3);
  copyKeyBtn?.addEventListener("click", copyKey);
  downloadKeyBtn?.addEventListener("click", downloadKey);
  applyInviteCodeBtn?.addEventListener("click", applyInviteCode);
  inviteCodeInput?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      applyInviteCode();
    }
  });
  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    const copyValue = target.getAttribute("data-copy-value");
    if (!copyValue) {
      return;
    }
    navigator.clipboard.writeText(copyValue).catch(() => {});
  });
});
