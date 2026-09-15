// host shim: file-backed NVS so a PROCESS RESTART is a genuine reboot.
// Store file: $ASKQ_NVS_FILE (ns.key<TAB>value per line).
#pragma once
#include "esp_err.h"
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <map>
#include <string>
#include <fstream>
typedef uint32_t nvs_handle_t;
enum nvs_open_mode_t { NVS_READONLY, NVS_READWRITE };
namespace nvs_shim {
inline std::map<std::string,std::string> &store(){ static std::map<std::string,std::string> m; static bool loaded=false;
  if(!loaded){ loaded=true; const char*f=getenv("ASKQ_NVS_FILE"); if(f){ std::ifstream in(f); std::string line;
    while(std::getline(in,line)){ auto t=line.find('\t'); if(t!=std::string::npos) m[line.substr(0,t)]=line.substr(t+1); } } }
  return m; }
inline void flush(){ const char*f=getenv("ASKQ_NVS_FILE"); if(!f) return; std::ofstream out(f);
  for(auto&kv:store()) out<<kv.first<<'\t'<<kv.second<<'\n'; }
inline std::string &ns(){ static std::string n; return n; }
}
static inline esp_err_t nvs_open(const char*name, nvs_open_mode_t, nvs_handle_t*h){ nvs_shim::ns()=name; *h=1; return ESP_OK; }
static inline void nvs_close(nvs_handle_t){}
static inline esp_err_t nvs_commit(nvs_handle_t){ nvs_shim::flush(); return ESP_OK; }
static inline esp_err_t nvs_set_u8(nvs_handle_t,const char*k,uint8_t v){ nvs_shim::store()[nvs_shim::ns()+"."+k]=std::to_string((int)v); return ESP_OK; }
static inline esp_err_t nvs_get_u8(nvs_handle_t,const char*k,uint8_t*v){ auto&m=nvs_shim::store(); auto it=m.find(nvs_shim::ns()+"."+k); if(it==m.end()) return ESP_ERR_NOT_FOUND; *v=(uint8_t)atoi(it->second.c_str()); return ESP_OK; }
static inline esp_err_t nvs_set_str(nvs_handle_t,const char*k,const char*v){ nvs_shim::store()[nvs_shim::ns()+"."+k]=v; return ESP_OK; }
static inline esp_err_t nvs_get_str(nvs_handle_t,const char*k,char*out,size_t*len){ auto&m=nvs_shim::store(); auto it=m.find(nvs_shim::ns()+"."+k); if(it==m.end()) return ESP_ERR_NOT_FOUND; if(it->second.size()+1>*len) return ESP_ERR_INVALID_SIZE; memcpy(out,it->second.c_str(),it->second.size()+1); *len=it->second.size()+1; return ESP_OK; }
static inline esp_err_t nvs_erase_key(nvs_handle_t,const char*k){ nvs_shim::store().erase(nvs_shim::ns()+"."+k); nvs_shim::flush(); return ESP_OK; }
