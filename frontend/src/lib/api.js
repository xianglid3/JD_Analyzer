
let refreshPromise = null

function refreshOnce(){
    if(!refreshPromise){
        refreshPromise = fetch('/api/auth/refresh', { method: 'POST', credentials: 'include' })
        .finally(() => { refreshPromise = null })   
    }
    return refreshPromise
}


export async function apiFetch(path, options = {}, retry = true) {

    const res = await fetch('/api' + path, {
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        ...options,
    })
        
    if (res.status === 401 && retry && path !== '/auth/refresh') {
        
        const refreshed = await refreshOnce()
        if (refreshed.ok){return apiFetch(path, options, false)}
    
    } 

    const data = await res.json().catch(() => null)   

    if (!res.ok) {                      
        throw new Error(data?.error || 'Request failed')
    }

    return data
}
