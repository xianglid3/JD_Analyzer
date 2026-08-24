
let refreshPromise = null

function refreshOnce(){
    if(!refreshPromise){
        refreshPromise = fetch('/api/auth/refresh', { method: 'POST', credentials: 'include' })
        .finally(() => { refreshPromise = null })   
    }
    return refreshPromise
}


export async function apiFetch(path, options = {}, retry = true) {

    // FormData (file upload) must NOT get a JSON content-type — the browser sets
    // multipart with the correct boundary itself.
    const isFormData = options.body instanceof FormData
    const { headers: optionHeaders = {}, ...fetchOptions } = options

    const res = await fetch('/api' + path, {
        ...fetchOptions,
        credentials: 'include',
        headers: {
            ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
            ...optionHeaders,
        },
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
