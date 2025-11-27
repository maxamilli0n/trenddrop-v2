(function () {
  const body = document.body;
  const dataset = body ? body.dataset : {};
  const origin = typeof window !== "undefined" ? window.location.origin.replace(/\/$/, "") : "";

  const config = {
    checkoutEndpoint: (dataset?.checkoutEndpoint || "").trim(),
    priceId: (dataset?.priceId || "").trim(),
    planId: (dataset?.planId || "premium_telegram").trim(),
    productName: (dataset?.productName || "TrendDrop Premium Access").trim(),
    successUrl: (dataset?.successUrl || `${origin}/premium-success.html`).trim(),
    cancelUrl: (dataset?.cancelUrl || `${origin}/premium.html`).trim(),
    inviteFallback: (dataset?.inviteFallback || "").trim(),
  };

  const statusNodes = Array.from(document.querySelectorAll("[data-premium-status]"));
  const showStatus = (message) => {
    statusNodes.forEach((node) => {
      node.textContent = message;
      if (message) {
        node.hidden = false;
      } else {
        node.hidden = true;
      }
    });
  };

  const ctas = Array.from(document.querySelectorAll("[data-premium-cta]"));
  ctas.forEach((button) => {
    button.addEventListener("click", async () => {
      if (!config.checkoutEndpoint || !config.priceId) {
        showStatus("Configure your checkout endpoint and Stripe price ID.");
        return;
      }
      showStatus("");
      const previous = button.textContent;
      button.disabled = true;
      button.textContent = "Redirecting…";
      try {
        const res = await fetch(config.checkoutEndpoint, {
          method: "POST",
          headers: {
            "content-type": "application/json",
          },
          body: JSON.stringify({
            priceId: config.priceId,
            successUrl: config.successUrl,
            cancelUrl: config.cancelUrl,
            planId: config.planId,
            productName: config.productName,
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          throw new Error(data?.error || `Request failed (${res.status})`);
        }
        if (data?.url) {
          window.location.href = data.url;
          return;
        }
        throw new Error("Missing Stripe redirect URL");
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        console.error("[premium] checkout error", message);
        showStatus(message || "Could not start checkout. Try again.");
        button.disabled = false;
        button.textContent = previous;
      }
    });
  });

  const successCard = document.querySelector("[data-premium-success]");
  if (successCard) {
    const fallbackWrapper = successCard.querySelector("[data-premium-fallback]");
    const fallbackLink = successCard.querySelector("[data-premium-fallback-link]");
    if (config.inviteFallback && fallbackLink) {
      fallbackLink.href = config.inviteFallback;
      fallbackLink.textContent = config.inviteFallback;
    } else if (fallbackWrapper) {
      fallbackWrapper.remove();
    }
  }
})();


