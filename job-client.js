'use strict';
// Shared, read-only job polling. Never retries an upload or publishing request.
(function(root){
  function remember(key,value){try{value?localStorage.setItem(key,value):localStorage.removeItem(key)}catch{}}
  function recalled(key){try{return localStorage.getItem(key)}catch{return null}}
  function watch(id,handlers,options={}){
    let cancelled=false,timer=null,failures=0,controller=null;
    const schedule=options.schedule||setTimeout,unschedule=options.unschedule||clearTimeout;
    async function tick(){
      if(cancelled)return;
      controller=new AbortController();
      const timeout=schedule(()=>controller.abort(),20000);
      try{
        const response=await (options.fetch||fetch)('/api/jobs/'+encodeURIComponent(id),{signal:controller.signal,cache:'no-store'});
        let data;try{data=await response.json()}catch{throw Error('The server is still responding. Reconnecting…')}
        if(cancelled)return;
        if(!response.ok){
          const error=Error(data.detail||'Could not check this video.');error.status=response.status;
          if(response.status>=400&&response.status<500&&response.status!==429){handlers.stopped(error);return}
          throw error;
        }
        failures=0;handlers.updated(data);
        if(!['completed','failed'].includes(data.status))timer=schedule(tick,2000);
      }catch(error){
        if(cancelled)return;
        failures++;
        if(failures>=8){handlers.stopped(Error('The connection is still unavailable. Press Check again to resume checking this video.'));return}
        handlers.retrying('Connection interrupted. Reconnecting to your existing video…');
        timer=schedule(tick,Math.min(15000,2000*failures));
      }finally{unschedule(timeout)}
    }
    tick();return ()=>{cancelled=true;unschedule(timer);if(controller)controller.abort()};
  }
  root.ClipJobs={watch,remember,recalled};
})(typeof window==='undefined'?globalThis:window);
