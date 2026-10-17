/* Chunked photo-ZIP uploader for the admin import pages: sends each ZIP in small pieces (Cloudflare rejects requests above 100 MB),
 * shows progress, keeps the submit button disabled until every ZIP has arrived and leaves the form usable meanwhile. */
(function(){
  var input=document.getElementById('photos_zip'); if(!input||!window.fetch) return;
  var form=input.form, token=document.getElementById('zip_token'), status=document.getElementById('zip_status'),
      bar=document.getElementById('zip_bar'), fill=document.getElementById('zip_fill'), submit=document.getElementById('import_submit'), submitLabel=submit.textContent,
      url=input.dataset.chunkUrl, size=parseInt(input.dataset.chunkSize,10), max=parseInt(input.dataset.maxBytes,10), maxTotal=parseInt(input.dataset.maxTotal,10), maxFiles=parseInt(input.dataset.maxFiles,10),
      csrf=(form.querySelector('[name=csrfmiddlewaretoken]')||{}).value, busy=false;
  function uid(){var a=new Uint8Array(16);crypto.getRandomValues(a);return Array.prototype.map.call(a,function(x){return ('0'+x.toString(16)).slice(-2);}).join('');}
  function mb(n){return (n/1048576).toFixed(n>=10485760?0:1)+' MB';}
  function ready(){busy=false; submit.disabled=false; submit.textContent=submitLabel;}
  function fail(msg){status.textContent=' '+msg; status.style.color='#b3392f'; bar.style.display='none'; token.value=''; ready(); input.name='photos_zip'; input.value=''; input.required=input.dataset.required==='1';}
  window.addEventListener('beforeunload',function(e){ if(busy){ e.preventDefault(); e.returnValue=''; } });
  function send(file,id,i,total,tries){
    var fd=new FormData(); fd.append('upload_id',id); fd.append('index',i); fd.append('total',total);
    fd.append('chunk',file.slice(i*size,Math.min(file.size,(i+1)*size)),'chunk');
    return fetch(url,{method:'POST',body:fd,credentials:'same-origin',headers:{'X-CSRFToken':csrf}}).then(function(r){
      return r.json().catch(function(){return {error:'Unexpected answer from the server.'};}).then(function(j){
        if(!r.ok){ if(r.status>=500&&tries<3) return send(file,id,i,total,tries+1); throw new Error(j.error||('Upload failed ('+r.status+')')); }
        return j; });
    },function(){ if(tries<3) return send(file,id,i,total,tries+1); throw new Error('The connection dropped. Check your internet and choose the files again.'); });
  }
  input.addEventListener('change',function(){
    token.value=''; status.style.color=''; status.textContent='';
    var files=Array.prototype.slice.call(input.files); if(!files.length) return;
    var all=files.reduce(function(n,f){return n+f.size;},0);
    for(var k=0;k<files.length;k++){ if(!/\.zip$/i.test(files[k].name)) return fail('Choose .zip files only.'); if(files[k].size>max) return fail(files[k].name+' is '+mb(files[k].size)+'; the limit per ZIP is '+mb(max)+'.'); }
    if(files.length>maxFiles) return fail('Choose at most '+maxFiles+' ZIP files.');
    if(all>maxTotal) return fail('Together the ZIPs are '+mb(all)+'; the limit is '+mb(maxTotal)+'.');
    busy=true; submit.disabled=true; bar.style.display='block'; fill.style.width='0'; input.removeAttribute('name'); input.required=false; // files go up in pieces; the form only carries the tokens
    var tokens=[], sent=0;
    (function nextFile(n){
      if(n>=files.length){ token.value=tokens.join(','); fill.style.width='100%'; status.textContent=' Uploaded ✓ '+files.length+' ZIP'+(files.length>1?'s':'')+' ('+mb(all)+')'; ready(); return; }
      var file=files[n], total=Math.max(1,Math.ceil(file.size/size)), id=uid(), i=0;
      (function nextChunk(){
        var done=sent+Math.min(file.size,i*size), pct=Math.round(100*done/all);
        status.textContent=' Uploading '+mb(all)+' in the background (ZIP '+(n+1)+' of '+files.length+') — you can keep filling in the form. '+pct+'%';
        fill.style.width=pct+'%'; submit.textContent='Uploading photos… '+pct+'%';
        send(file,id,i,total,0).then(function(j){
          i++; if(i<total) return nextChunk();
          if(!j.done) throw new Error('The upload did not finish. Choose the files again.');
          tokens.push(j.token); sent+=file.size; nextFile(n+1);
        }).catch(function(e){ fail(e.message); });
      })();
    })(0);
  });
  form.addEventListener('submit',function(e){ if(busy){ e.preventDefault(); status.textContent=' Please wait for the upload to finish…'; } });
})();
