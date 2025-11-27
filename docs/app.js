async function loadProducts(){
  try{
    const res = await fetch('data/products.json?cache=' + Date.now());
    const data = await res.json();
    const grid = document.getElementById('grid');
    grid.innerHTML = '';
    (data.products || []).forEach(p => {
      const card = document.createElement('div');
      card.className = 'card';
      const img = document.createElement('img');
      img.src = p.image_url || '';
      img.alt = p.title || '';
      const pad = document.createElement('div');
      pad.className = 'pad';
      const h3 = document.createElement('h3');
      h3.textContent = (p.headline || p.title || '');
      const blurb = document.createElement('p');
      blurb.className = 'blurb';
      blurb.textContent = (p.blurb || '');
      const price = document.createElement('div');
      price.className = 'price';
      const pr = (p.price !== undefined && p.currency) ? `${p.currency} ${p.price.toFixed(2)}` : '';
      price.textContent = pr;
      const a = document.createElement('a');
      a.className = 'btn';
      a.href = (p.click_url || p.url || '#');
      a.target = '_blank';
      a.rel = 'nofollow sponsored noopener';
      a.textContent = 'View';
      pad.appendChild(h3);
      if (blurb.textContent) pad.appendChild(blurb);
      pad.appendChild(price);
      pad.appendChild(a);
      card.appendChild(img);
      card.appendChild(pad);
      grid.appendChild(card);
    });
    const updated = document.getElementById('updated_at');
    const ts = data.updated_at ? new Date(data.updated_at * 1000) : new Date();
    updated.textContent = ts.toLocaleString();
    document.getElementById('year').textContent = new Date().getFullYear();

    // Premium button wiring (set via env var on the page if added)
    const premium = document.getElementById('goPremium');
    if (premium){
      const configured = (window.PREMIUM_CHECKOUT_URL || '').trim();
      const targetUrl = configured || 'premium.html';
      premium.href = targetUrl;
      if (configured && /^https?:/i.test(configured)){
        premium.target = '_blank';
        premium.rel = 'nofollow noopener';
      }else{
        premium.removeAttribute('target');
        premium.rel = 'nofollow noopener';
      }
    }
  }catch(e){
    console.error(e);
  }
}
loadProducts();
