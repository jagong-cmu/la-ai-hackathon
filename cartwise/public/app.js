const form=document.querySelector("#comparison-form");
const compareButton=document.querySelector("#compare");
const screens={setup:document.querySelector("#screen-setup"),agents:document.querySelector("#screen-agents"),results:document.querySelector("#screen-results")};
const navButtons=[...document.querySelectorAll(".step")];
const unlocked=new Set(["setup"]);
const value=id=>document.querySelector(`#${id}`).value.trim();
const money=cents=>cents===null?"Unavailable":new Intl.NumberFormat("en-US",{style:"currency",currency:"USD"}).format(cents/100);
const productList=document.querySelector("#product-list");
const supplierList=document.querySelector("#supplier-list");
const cancelButton=document.querySelector("#cancel-job");
let itemSequence=productList.querySelectorAll("[data-item-row]").length;
let supplierSequence=supplierList.querySelectorAll("[data-supplier-row]").length;
let activeRequest=null;
let currentJobId=null;

function showScreen(name){
  if(!unlocked.has(name))return;
  for(const [key,screen] of Object.entries(screens))screen.classList.toggle("active",key===name);
  navButtons.forEach((button,index)=>{const target=button.dataset.go;button.classList.toggle("active",target===name);button.classList.toggle("done",index<["setup","agents","results"].indexOf(name));button.disabled=!unlocked.has(target)});
  window.scrollTo({top:0,behavior:"smooth"});
}
document.querySelectorAll("[data-go]").forEach(button=>button.addEventListener("click",event=>{event.preventDefault();showScreen(button.dataset.go)}));

function readRow(row,role){return row.querySelector(`[data-role="${role}"]`).value.trim()}
function buildRequest(){return{items:[...productList.querySelectorAll("[data-item-row]")].map(row=>({id:row.dataset.id,description:readRow(row,"description"),quantity:Number(readRow(row,"quantity")),unit:readRow(row,"unit")})),suppliers:[...supplierList.querySelectorAll("[data-supplier-row]")].map(row=>({id:row.dataset.id,name:readRow(row,"name"),url:readRow(row,"url")})),address:{line1:value("address-line1"),city:value("address-city"),state:value("address-state").toUpperCase(),postalCode:value("address-postal-code"),country:"US"},contact:{firstName:value("contact-first-name"),lastName:value("contact-last-name"),email:value("contact-email"),phone:value("contact-phone")}}}

function updateLists(){
  const products=[...productList.querySelectorAll("[data-item-row]")],suppliers=[...supplierList.querySelectorAll("[data-supplier-row]")];
  products.forEach((row,index)=>{row.querySelector("label").childNodes[0].textContent=`Product ${index+1}`;const remove=row.querySelector("[data-remove]");remove.disabled=products.length===1;remove.setAttribute("aria-label",`Remove product ${index+1}`)});
  suppliers.forEach(row=>row.querySelector("[data-remove]").disabled=suppliers.length===1);
}

function addProduct(){
  itemSequence+=1;const row=document.createElement("div");row.className="row product-row";row.dataset.itemRow="";row.dataset.id=`item-${itemSequence}`;
  row.innerHTML='<label>Product<input data-role="description" required placeholder="Product description" /></label><label>Qty<input data-role="quantity" required type="number" min="1" step="1" value="1" /></label><label>Unit<input data-role="unit" required placeholder="unit" /></label><button class="remove-button" type="button" data-remove="item" aria-label="Remove product">×</button>';
  productList.append(row);updateLists();row.querySelector("input").focus();
}

function addSupplier(){
  supplierSequence+=1;const row=document.createElement("div");row.className="row supplier-row";row.dataset.supplierRow="";row.dataset.id=`supplier-${supplierSequence}`;
  row.innerHTML='<label>Name<input data-role="name" required placeholder="Supplier name" /></label><label>Website<input data-role="url" required type="url" placeholder="https://supplier.com" /></label><button class="remove-button" type="button" data-remove="supplier" aria-label="Remove supplier">×</button>';
  supplierList.append(row);updateLists();row.querySelector("input").focus();
}

document.querySelector("#add-product").addEventListener("click",addProduct);
document.querySelector("#add-supplier").addEventListener("click",addSupplier);
document.querySelector("#comparison-form").addEventListener("click",event=>{const button=event.target.closest("[data-remove]");if(!button||button.disabled)return;button.closest(".row").remove();updateLists()});
updateLists();

function renderJob(job){
  document.querySelector("#mode").textContent=`${job.mode} mode`;
  document.querySelector("#status").textContent=({queued:"Queued",discovering:"Finding products",optimizing:"Building plans",verifying:"Checking totals",cancelling:"Cancelling",cancelled:"Cancelled",complete:"Complete",failed:"Failed"})[job.status]||job.status;
  document.querySelector("#progress").textContent=`${job.progress.completed} / ${job.progress.total}`;
  document.querySelector("#message").textContent=job.message;
  const phase={queued:5,discovering:15,optimizing:45,verifying:50,cancelling:95,cancelled:100,complete:100,failed:100}[job.status]||5;
  const ratio=job.progress.total?job.progress.completed/job.progress.total:0;
  const measured=job.status==="discovering"?15+ratio*30:job.status==="verifying"?50+ratio*50:phase;
  document.querySelector("#progress-bar").style.width=`${Math.min(100,Math.max(phase,measured))}%`;
  document.querySelector("#status-dot").style.background=job.status==="failed"?"#b42318":job.status==="cancelled"?"#8a8179":"";
  const terminal=["complete","failed","cancelled"].includes(job.status);cancelButton.hidden=terminal;cancelButton.disabled=terminal||job.status==="cancelling"||!currentJobId;cancelButton.textContent=job.status==="cancelling"?"Cancelling…":"Cancel";
  renderBrowserViews(job.browserViews||[]);
  if(job.result){renderResult(job.result);unlocked.add("results");showScreen("results")}
}

function renderBrowserViews(views){
  const visible=views,container=document.querySelector("#browser-views"),empty=document.querySelector("#browser-empty");empty.hidden=visible.length>0;
  const ids=new Set(visible.map(view=>view.runId));for(const child of [...container.children])if(!ids.has(child.dataset.runId))child.remove();
  for(const view of visible){let panel=container.querySelector(`[data-run-id="${CSS.escape(view.runId)}"]`);if(!panel){panel=document.createElement("article");panel.className="browser-view";panel.dataset.runId=view.runId;
      const bar=document.createElement("div"),dots=document.createElement("span"),title=document.createElement("span"),badge=document.createElement("span"),stage=document.createElement("div");bar.className="browser-bar";dots.className="browser-dots";dots.innerHTML="<i></i><i></i><i></i>";title.className="browser-title";badge.className="view-status";stage.className="browser-stage";stage.inert=true;bar.append(dots,title,badge);panel.append(bar,stage);container.append(panel)}
    const title=panel.querySelector(".browser-title"),badge=panel.querySelector(".view-status"),stage=panel.querySelector(".browser-stage");title.textContent=view.label;badge.textContent=view.status;badge.className=`view-status ${view.status}`;
    const stopped=["cancelled","failed"].includes(view.status);if(view.liveUrl&&!stopped){let frame=stage.querySelector("iframe");stage.querySelector(".browser-placeholder")?.remove();if(!frame){frame=document.createElement("iframe");frame.tabIndex=-1;frame.allow="autoplay";frame.referrerPolicy="no-referrer";stage.append(frame)}frame.title=view.label;if(frame.src!==view.liveUrl)frame.src=view.liveUrl}else{const oldFrame=stage.querySelector("iframe");if(oldFrame){oldFrame.src="about:blank";oldFrame.remove()}let placeholder=stage.querySelector(".browser-placeholder");if(!placeholder){placeholder=document.createElement("div");placeholder.className="browser-placeholder";stage.append(placeholder)}placeholder.textContent=view.status==="queued"?"Waiting to start…":view.status==="cancelled"?"Agent cancelled.":view.status==="failed"?"Agent stopped.":"Opening browser…"}}
}

function renderEvidence(result,winner,request){
  const section=document.querySelector("#evidence-section"),grid=document.querySelector("#evidence-grid");grid.replaceChildren();
  if(!winner){section.hidden=true;return}
  const quoteKeys=[...new Set(winner.quoteKeys)];
  for(const key of quoteKeys){const quote=result.quotes.find(candidate=>candidate.key===key);if(!quote)continue;
    const supplier=request.suppliers.find(candidate=>candidate.id===quote.supplierId),card=document.createElement("article"),heading=document.createElement("div"),name=document.createElement("strong"),badge=document.createElement("span");card.className="evidence-card";heading.className="evidence-card-heading";name.textContent=supplier?.name||quote.supplierId;badge.className=`evidence-status ${quote.status}`;badge.textContent=quote.status;heading.append(name,badge);card.append(heading);
    const media=document.createElement("div"),missing=document.createElement("span");media.className="evidence-media";missing.className="evidence-missing";missing.textContent="Screenshot unavailable";
    if(quote.evidence.screenshotUrl){const image=document.createElement("img");image.alt=`Checkout cost summary from ${supplier?.name||quote.supplierId}`;image.loading="lazy";image.src=quote.evidence.screenshotUrl;missing.hidden=true;image.addEventListener("error",()=>{image.hidden=true;missing.hidden=false});media.append(image,missing)}else media.append(missing);card.append(media);
    const costs=document.createElement("dl");costs.className="evidence-costs";for(const [label,amount] of [["Subtotal",quote.costs.subtotalCents],["Shipping",quote.costs.shippingCents],["Tax",quote.costs.taxCents],["Total",quote.costs.totalCents]]){const group=document.createElement("div"),term=document.createElement("dt"),value=document.createElement("dd");term.textContent=label;value.textContent=money(amount);group.append(term,value);costs.append(group)}card.append(costs);
    if(quote.evidence.checkoutUrl){const link=document.createElement("a");link.className="evidence-link";link.href=quote.evidence.checkoutUrl;link.target="_blank";link.rel="noreferrer";link.textContent="Open checkout ↗";card.append(link)}grid.append(card)}
  section.hidden=grid.children.length===0;
}

function renderResult(result){
  const request=activeRequest||buildRequest();document.querySelector("#winning-total").textContent=money(result.lowestVerifiedTotalCents);document.querySelector("#single-total").textContent=money(result.bestSingleSupplierTotalCents);document.querySelector("#savings-total").textContent=money(result.savingsCents);document.querySelector("#scope-value").textContent=({all_feasible_verified:"Fully verified",partial_verified:"Partial verification",none_verified:"Unverified"})[result.scope];document.querySelector("#total-label").textContent=result.scope==="all_feasible_verified"?"Lowest verified total":result.scope==="partial_verified"?"Best verified total":"No verified total";document.querySelector("#savings-pill").textContent=result.savingsCents===null?"No baseline":result.savingsCents>0?`Save ${money(result.savingsCents)}`:"Best single supplier";
  const winner=result.plans.find(plan=>plan.id===result.winnerPlanId),container=document.querySelector("#plan");container.replaceChildren();
  if(winner){const list=document.createElement("ul");list.className="allocation-list";for(const allocation of winner.allocations){const product=request.items.find(item=>item.id===allocation.itemId),supplier=request.suppliers.find(item=>item.id===allocation.supplierId),row=document.createElement("li"),item=document.createElement("span"),source=document.createElement("span");row.className="allocation-row";item.className="allocation-item";item.textContent=product?.description||allocation.itemId;source.className="allocation-supplier";source.textContent=`${supplier?.name||allocation.supplierId} · ${allocation.packs} pack${allocation.packs===1?"":"s"}`;row.append(item,source);list.append(row)}container.append(list)}else container.textContent="No verified plan available.";
  const notice=document.querySelector("#limitations");notice.replaceChildren();if(result.scope==="partial_verified"){const summary=document.createElement("strong");summary.textContent="Best verified result — not a proven global minimum.";notice.append(summary)}if(result.limitations.length){const list=document.createElement("ul");for(const limitation of result.limitations){const item=document.createElement("li");item.textContent=limitation;list.append(item)}notice.append(list)}notice.hidden=notice.children.length===0;renderEvidence(result,winner,request);document.querySelector("#raw").textContent=JSON.stringify(result,null,2);
}

async function poll(jobId){for(;;){const response=await fetch(`/api/comparisons/${encodeURIComponent(jobId)}`);if(!response.ok)throw new Error((await response.json()).error||"Could not read job");const job=await response.json();renderJob(job);if(["complete","failed","cancelled"].includes(job.status)){if(job.status==="failed")throw new Error(job.error||"Comparison failed");return}await new Promise(resolve=>setTimeout(resolve,2000))}}

form.addEventListener("submit",async event=>{event.preventDefault();compareButton.disabled=true;currentJobId=null;cancelButton.hidden=false;cancelButton.disabled=true;document.querySelector("#error").hidden=true;unlocked.add("agents");renderBrowserViews([]);showScreen("agents");try{activeRequest=buildRequest();const response=await fetch("/api/comparisons",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(activeRequest)}),data=await response.json();if(!response.ok)throw new Error(data.error||"Could not start comparison");currentJobId=data.jobId;cancelButton.disabled=false;await poll(data.jobId)}catch(error){document.querySelector("#message").textContent=error instanceof Error?error.message:String(error);document.querySelector("#status").textContent="Failed";document.querySelector("#status-dot").style.background="#b42318"}finally{compareButton.disabled=false}});

cancelButton.addEventListener("click",async()=>{if(!currentJobId||cancelButton.disabled)return;cancelButton.disabled=true;cancelButton.textContent="Cancelling…";document.querySelector("#status").textContent="Cancelling";document.querySelector("#message").textContent="Stopping active agents…";try{const response=await fetch(`/api/comparisons/${encodeURIComponent(currentJobId)}/cancel`,{method:"POST"}),job=await response.json();if(!response.ok)throw new Error(job.error||"Could not cancel comparison");renderJob(job)}catch(error){cancelButton.disabled=false;cancelButton.textContent="Cancel";document.querySelector("#status").textContent="Cancel failed";document.querySelector("#message").textContent=error instanceof Error?error.message:String(error)}});
fetch("/api/health").then(response=>response.json()).then(health=>{document.querySelector("#mode").textContent=`${health.mode} mode`}).catch(()=>{document.querySelector("#mode").textContent="offline"});
