#pragma once
#include <utility>
namespace rkstream {
template<class F> class ScopeExit {
public:
    explicit ScopeExit(F f) : callback_(std::move(f)) {}
    ~ScopeExit() noexcept { if (active_) { try { callback_(); } catch (...) {} } }
    ScopeExit(const ScopeExit&) = delete;
    ScopeExit& operator=(const ScopeExit&) = delete;
    void dismiss() noexcept { active_ = false; }
private:
    F callback_;
    bool active_ = true;
};
}
