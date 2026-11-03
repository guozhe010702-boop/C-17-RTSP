#include "rkstream/v4l2_dmabuf_capture.h"
#include "rkstream/bounded_latest_queue.h"
#include <cassert>
#include <stdexcept>
#include <type_traits>
#include <iostream>
using namespace rkstream;
struct FakeDriver { int returns=0; };
bool give_back(void* owner,const DmabufFrame&,std::string*) {
    ++static_cast<FakeDriver*>(owner)->returns; return true;
}
DmabufFrameLease acquire(FakeDriver& driver) { return {{}, &driver, give_back}; }
int main() {
    static_assert(!std::is_copy_constructible<DmabufFrameLease>::value, "lease must not copy");
    static_assert(std::is_nothrow_move_constructible<DmabufFrameLease>::value, "queue moves must not throw");
    FakeDriver driver;
    {
        auto a=acquire(driver);
        auto b=std::move(a);
        assert(!a.owns_buffer() && b.owns_buffer());
        assert(b.release()); assert(b.release());
    }
    assert(driver.returns==1);
    {
        BoundedLatestQueue<DmabufFrameLease> q(1);
        assert(q.push(acquire(driver)).accepted);
        { auto drop=q.push(acquire(driver)); assert(drop.dropped_oldest); }
        assert(driver.returns==2);
        q.close();
        assert(!q.push(acquire(driver)).accepted); // rejected parameter is destroyed
        assert(driver.returns==3);
        { auto pending=q.drain(); assert(pending.size()==1); }
    }
    assert(driver.returns==4);
    try { auto frame=acquire(driver); throw std::runtime_error("worker failure"); }
    catch(const std::runtime_error&) {}
    assert(driver.returns==5);
    { auto a=acquire(driver); auto b=acquire(driver); b=std::move(a); }
    assert(driver.returns==7);
    std::cout<<"Lease move/drop/reject/drain/exception cleanup: PASS\n";
}
