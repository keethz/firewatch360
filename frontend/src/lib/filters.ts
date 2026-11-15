// App-wide detection filter: drop presumed agricultural burning (cells that are mostly cropland or
// pasture in NLCD 2021). Appended to every API call by withAoi; endpoints that do not use it ignore it.

const KEY = 'firewatch360.excludeAgriculture'
let excludeAg = (() => {
  try {
    return localStorage.getItem(KEY) === '1'
  } catch {
    return false
  }
})()

export const getExcludeAg = () => excludeAg

export function setExcludeAg(v: boolean) {
  excludeAg = v
  try {
    localStorage.setItem(KEY, v ? '1' : '0')
  } catch {
    /* storage unavailable: setting lasts for this page only */
  }
}

export const filterQuery = () => (excludeAg ? 'exclude_agriculture=true' : '')
