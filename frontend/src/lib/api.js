
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
    const {
        headers: optionHeaders = {},
        timeoutMs,
        signal: optionSignal,
        ...fetchOptions
    } = options
    const timeoutController = timeoutMs && !optionSignal ? new AbortController() : null
    const timeoutId = timeoutController
        ? window.setTimeout(() => timeoutController.abort(), timeoutMs)
        : null

    let res
    try {
        res = await fetch('/api' + path, {
            ...fetchOptions,
            credentials: 'include',
            signal: optionSignal || timeoutController?.signal,
            headers: {
                ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
                ...optionHeaders,
            },
        })
    } catch (error) {
        if (timeoutController?.signal.aborted && error?.name === 'AbortError') {
            const timeoutError = new Error('Request timed out. Please try again.')
            timeoutError.status = 408
            throw timeoutError
        }
        throw error
    } finally {
        if (timeoutId !== null) window.clearTimeout(timeoutId)
    }
        
    if (res.status === 401 && retry && path !== '/auth/refresh') {
        
        const refreshed = await refreshOnce()
        if (refreshed.ok){return apiFetch(path, options, false)}
    
    } 

    const data = await res.json().catch(() => null)   

    if (!res.ok) {
        const error = new Error(data?.error || 'Request failed')
        error.status = res.status
        throw error
    }

    return data
}

export function apiUpload(path, formData, onProgress, retry = true) {
    return new Promise((resolve, reject) => {
        const request = new XMLHttpRequest()
        request.open('POST', '/api' + path)
        request.withCredentials = true

        request.upload.addEventListener('progress', (event) => {
            if (!event.lengthComputable) return
            onProgress?.(Math.round((event.loaded / event.total) * 100))
        })

        request.addEventListener('load', async () => {
            if (request.status === 401 && retry && path !== '/auth/refresh') {
                try {
                    const refreshed = await refreshOnce()
                    if (refreshed.ok) {
                        resolve(apiUpload(path, formData, onProgress, false))
                        return
                    }
                } catch {
                    // Fall through and return the original authentication error.
                }
            }

            let data
            try {
                data = request.responseText ? JSON.parse(request.responseText) : null
            } catch {
                data = null
            }

            if (request.status < 200 || request.status >= 300) {
                const error = new Error(data?.error || 'Upload failed')
                error.status = request.status
                reject(error)
                return
            }

            resolve(data)
        })

        request.addEventListener('error', () => {
            const error = new Error('Upload failed. Check your connection and try again.')
            error.status = 0
            reject(error)
        })

        request.send(formData)
    })
}
