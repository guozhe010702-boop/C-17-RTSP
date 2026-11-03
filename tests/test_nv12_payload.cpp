#include "rkstream/nv12_payload.h"
#include <cassert>
#include <vector>
#include <iostream>
using namespace rkstream;
int main() {
    std::vector<std::byte> y(32, std::byte{0x7f}), uv(24, std::byte{0x7f}), out;
    // 4x4, Y stride 6, UV stride 8; both have a nonzero data_offset.
    for (int r=0; r<4; ++r) for (int c=0; c<4; ++c) y[2+r*6+c]=std::byte(r*4+c);
    for (int r=0; r<2; ++r) for (int c=0; c<4; ++c) uv[3+r*8+c]=std::byte(40+r*4+c);
    std::vector<PlanePayload> p{{y.data(),y.size(),26,2,6},{uv.data(),uv.size(),19,3,8}};
    std::string error;
    assert(copy_nv12_payload(p,4,4,&out,&error));
    assert(out.size()==36);
    for (int r=0; r<4; ++r) for(int c=0;c<4;++c) assert(out[r*6+c]==std::byte(r*4+c));
    for (int r=0; r<2; ++r) for(int c=0;c<4;++c) assert(out[24+r*6+c]==std::byte(40+r*4+c));
    auto bad=p; bad[1].bytes_used=14;
    assert(!copy_nv12_payload(bad,4,4,&out,&error));
    bad=p; bad[0].bytes_used=100;
    assert(!copy_nv12_payload(bad,4,4,&out,&error));
    bad=p; bad[1].data_offset=20;
    assert(!copy_nv12_payload(bad,4,4,&out,&error));
    bad=p; bad[1].stride=2;
    assert(!copy_nv12_payload(bad,4,4,&out,&error));
    assert(!copy_nv12_payload(p,3,4,&out,&error));
    std::vector<std::byte> contiguous(38, std::byte{0x55});
    p={{contiguous.data(),38,38,2,6}};
    assert(copy_nv12_payload(p,4,4,&out,&error));
    assert(out[35]==std::byte{0x55});
    p[0].bytes_used=34;
    assert(!copy_nv12_payload(p,4,4,&out,&error));
    p[0].bytes_used=0; // documented raw-buffer fallback remains bounded by map length
    assert(copy_nv12_payload(p,4,4,&out,&error));
    std::cout<<"NV12/NV12M offsets, strides and malformed payloads: PASS\n";
}
